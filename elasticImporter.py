# -*- coding: utf-8 -*-
# encoding=utf8
import sys
if sys.version_info < (3,0,0):
	reload(sys)
	sys.setdefaultencoding('utf8')
from elasticsearch import Elasticsearch, helpers
from elasticsearch_dsl import *
from elasticsearch_dsl.connections import connections
import fileinput, logging, argparse, gc, codecs, json, math, hashlib, signal, os, traceback
from argparse import RawTextHelpFormatter
from datetime import datetime

args = None
translate_cfg_property = None
version = None

def parse_args():
	parser = argparse.ArgumentParser(description='This program indexes files to elasticsearch.\n', formatter_class=RawTextHelpFormatter)
	parser.add_argument('-i', '--input', dest='input', required=False, default='-', help='Input file. Default: stdin.')
	parser.add_argument('-c', '--cfg', dest='cfg', required=True, help='Configuration file.')
	parser.add_argument('-s', '--separator', dest='separator', required=False, default=';', help='File Separator. Default: ;')
	#override configuration stuff
	parser.add_argument('-x', '--index', dest='index', required=False, default=None, help='Elasticsearch index. It overrides the cfg JSON file values. Default: the index specified in the JSON file.')
	parser.add_argument('-t', '--type', dest='type', required=False, default=None, help='Elasticsearch document type. It overrides the cfg JSON file values. Default: the type specified in the JSON file.')
	#elastic connection stuff
	parser.add_argument('-n', '--node', dest='node', required=False, default='localhost', help='Elasticsearch node. Default: localhost')
	parser.add_argument('-p', '--port', dest='port', required=False, default=9200, help='Elasticsearch port. Default: 9200')
	parser.add_argument('-u', '--user', dest='user', required=False, default=None, help='Elasticsearch user if needed.')
	parser.add_argument('-P', '--password', dest='password', required=False, default='', help='Elasticsearch password if needed.')
	#extra stuff to consider when indexing
	parser.add_argument('--skip_first_line', dest='skip_first_line', default=False, action='store_true', help='Skips first line.')
	parser.add_argument('--dates_in_seconds', dest='dates_in_seconds', default=False, action='store_true', help='If true, assume dates are provided in seconds.')
	parser.add_argument('--refresh', dest='refresh', default=False, action='store_true', help='Refresh the index when finished.')
	parser.add_argument('--delete', dest='delete', default=False, action='store_true', help='Delete the index before process.')
	#meta stuff to consider when creating indices
	parser.add_argument('--replicas', dest='replicas', default=0, help='Number of replicas for the index if it does not exist. Default: 0')
	parser.add_argument('--shards', dest='shards', default=2, help='Number of shards for the index if it does not exist. Default: 2')
	parser.add_argument('--refresh_interval', dest='refresh_interval', default='60s', help='Refresh interval for the index if it does not exist. Default: 60s')
	#index sutff for elastic
	parser.add_argument('--bulk', dest='bulk', required=False, default=2000, help='Elasticsearch bulk size parameter. Default: 2000')
	parser.add_argument('--threads', dest='threads', required=False, default=5, help='Number of threads for the parallel bulk. Default: 5')
	parser.add_argument('--queue', dest='queue', required=False, default=5, help='Size of queue for the parallel bulk. Default: 6')
	parser.add_argument('--timeout', dest='timeout', required=False, default=600, help='Connection timeout in seconds. Default: 600')
	#internal stuff for the elastic API
	parser.add_argument('--debug', dest='debug', default=False, action='store_true', help='If true log level is set to DEBUG.')
	parser.add_argument('--show_elastic_logger', dest='show_elastic_logger', default=False, action='store_true', help='If true show elastic logger at the same loglevel as the importer.')
	parser.add_argument('--raise_on_error', dest='raise_on_error', default=False, action='store_true', help='Raise BulkIndexError containing errors (as .errors) from the execution of the last chunk when some occur. By default we DO NOT raise.')
	parser.add_argument('--raise_on_exception', dest='raise_on_exception', default=False, action='store_true', help='By default we DO NOT propagate exceptions from call to bulk and just report the items that failed as failed. Use this option to propagate exceptions.')
	#stuff to avoid duplicates
	parser.add_argument('--md5_id', dest='md5_id', default=False, action='store_true', help='Uses the MD5 hash of the line as ID.')
	parser.add_argument('--md5_exclude', dest='md5_exclude', nargs = '*', required=False, default=[], help='List of column names to be excluded from the hash.')
	args = parser.parse_args()
	return args

def translate_cfg_property_2x(v):
	if v == 'date':
		return Date()
	elif v == 'text':
		return String()
	elif v == 'keyword':
		return String(index="not_analyzed")
	elif v == 'integer':
		return Integer()
	elif v == 'long':
		return Long()
	elif v == 'float':
		return Float()
	elif v == 'geopoint':
		return GeoPoint()
	elif v == 'ip':
		return Ip()

def translate_cfg_property_std(v):
	if v == 'date':
		return Date()
	elif v == 'text':
		return Text()
	elif v == 'keyword':
		return Keyword()
	elif v == 'integer':
		return Integer()
	elif v == 'long':
		return Long()
	elif v == 'float':
		return Float()
	elif v == 'geopoint':
		return GeoPoint()
	elif v == 'ip':
		return Ip()

def create_doc_class(cfg, doc_type):
	#create class
	dicc = {}
	for k, v in cfg['properties'].items():
		dicc[k] = translate_cfg_property(v)
	DocClass = type(doc_type, (DocType,), dicc)
	return DocClass

def is_nan_or_inf(value):
	if math.isinf(value) or math.isnan(value):
		logging.debug('Nan or inf encountered in value: |{}|.'.format(value))
		return True
	else:
		return False

numeric_properties = set(('integer', 'long', 'date', 'float'))
def parse_property(str_value, t, args):
	try:
		if t in numeric_properties:
			if str_value == '':
				return None
			float_value = float(str_value)
			if is_nan_or_inf(float_value):
				return None
		if t == 'integer':
			return int(float_value)
		if t == 'long':
			return long(float_value)
		elif t == 'date':
			return int(float_value*1000) if args.dates_in_seconds else int(float_value)
		elif t == 'float':
			return float_value
		else: # t == 'text' or t == 'keyword' or t == 'ip' or t == 'geopoint':
			return str_value
	except ValueError:
		logging.warn('ValueError processing value |{}| of type |{}| ignoring this field.'.format(str_value, t))
		return None
	except TypeError:
		logging.warn('TypeError processing value |{}| of type |{}| ignoring this field.'.format(str_value, t))
		return None

def input_generator(cfg, index, doc_type, args):
	properties = cfg['properties']
	fields = cfg['order_in_file']
	n_fields = len(cfg['order_in_file'])
	try:
		if args.input != '-':
			f = codecs.open(args.input, buffering=1, encoding='utf-8', errors='ignore')
		else:
			f = sys.stdin
	except IOError as e:
		logging.error('Error with the input file |{}|, Details: {}.'.format(args.input, sys.exc_info()[0]))
		return

	ctr = 0
	try:
		for line in f:
			try:
				if ctr == 0 and args.skip_first_line:
					continue
				ctr+=1
				sline = line.rstrip().split(args.separator)
				dicc = {fields[i]: parse_property(value, properties[fields[i]], args) for i, value in enumerate(sline)}
				#dicc = {k : dicc[k] for k in dicc if dicc[k] is not None} #remove nones
				a = {
					'_source' : dicc,
					'_index'  : index,
					'_type'   : doc_type
				}

				if args.md5_id:
					md5_dicc = {}
					for idx, field in enumerate(fields):
						if field not in args.md5_exclude:
							md5_dicc[field] = dicc[field]
					a['_id'] = hashlib.md5(json.dumps(md5_dicc)).hexdigest()
				yield a

			except ValueError as e:
				logging.warn('Error processing line |{}| ({}). Ignoring line.'.format(line, ctr))
				continue
			except Exception as e:
				logging.warn('Error processing line |{}| ({}). Ignoring line. Details {}'.format(line, ctr, sys.exc_info()[0]))
				traceback.print_exc(file=sys.stderr)
				continue
	except UnicodeDecodeError as e:
		logging.warn('UnicodeDecodeError processing the line after |{}| ({})'.format(line, ctr, sys.exc_info()[0]))
		traceback.print_exc(file=sys.stderr)
		return

if __name__ == '__main__':
	#load parameters
	args = parse_args()

	#set up loggers
	if not args.show_elastic_logger:
		for _ in ("elasticsearch", "urllib3"):
			logging.getLogger(_).setLevel(logging.CRITICAL)

	pid = os.getpid()
	def signal_handler(signal, frame):
			logging.error('You pressed Ctrl+C! Aborting execution.')
			os.kill(pid, 9)

	signal.signal(signal.SIGINT, signal_handler)

	loglevel = logging.DEBUG if args.debug else logging.INFO
	logging.basicConfig(format="[ %(asctime)s %(levelname)s %(threadName)s ] " + "%(message)s", level=loglevel)
	#logging.basicConfig(format='%(asctime)s %(message)s', level=loglevel)

	if args.user is None:
		es = Elasticsearch(args.node, timeout=args.timeout, port=args.port)
	else:
		es = Elasticsearch(args.node, timeout=args.timeout, port=args.port, http_auth=(args.user, args.password))
	full_version = es.info()['version']['number']
	version = int(full_version.split('.')[0])

	if version == 1:
		logging.error('Elasticsearch version 1.x is not supported.')

	logging.info('Using elasticsearch version {}'.format(full_version))

	translate_cfg_property = translate_cfg_property_2x if version == 2 else translate_cfg_property_std

	#load cfg file
	cfg = json.load(open(args.cfg))
	index = cfg['meta']['index'] if args.index is None else args.index
	doc_type = str(cfg['meta']['type']) if args.type is None else args.type
	#create class from the cfg
	#this class is used to initialize the mapping
	DocClass = create_doc_class(cfg, doc_type)
	#connection to elasticsearch
	if args.user is None:
		connections.create_connection(hosts=[args.node], timeout=args.timeout, port=args.port) #connection for api
	else:
		connections.create_connection(hosts=[args.node], timeout=args.timeout, port=args.port, http_auth=(args.user, args.password)) #connection for api

	#delete before doing anything else
	if args.delete:
		log.warning('Deleting index {} whether it exists or not...'.format(index))
		es.indices.delete(index=index, ignore=[400, 404])
	#initialize mapping
	index_obj = Index(index, using=es)
	if not index_obj.exists():
		index_obj.settings(
			number_of_replicas=args.replicas,
			number_of_shards=args.shards,
			refresh_interval=args.refresh_interval
		)
		index_obj.save()
	DocClass.init(index=index, using=es)
	#create the file iterator
	documents = input_generator(cfg, index, doc_type, args)
	ret = helpers.parallel_bulk(es, documents, raise_on_exception=args.raise_on_exception, thread_count=args.threads, queue_size=args.queue, chunk_size=args.bulk, raise_on_error=args.raise_on_error)
	failed_items = []
	failed = 0; success = 0; abs_ctr=0
	for ok, item in ret:
		abs_ctr+=1
		#STATS
		if ok:
			success+=1
		else:
			failed+=1
			failed_items.append(abs_ctr)
		#PROGRESS
		if (success+failed)%10000 == 0:
			logging.info('Success: {0}, Failed: {1}'.format(success, failed))

	logging.info('Success: {0}, Failed: {1}'.format(success, failed))

	if failed > 0:
		logging.error('There were some errors during the process: Success: {0}, Failed: {1}'.format(success, failed))
		logging.error('These were the errors in lines: {}'.format(failed_items))

	if args.refresh:
		es.indices.refresh(index=index)
