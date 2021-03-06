# -*- coding: utf-8 -*-
import pandas as pd
import sqlite3, argparse, os, logging, os.path, gzip, sys, gc, bisect
try:
    from StringIO import StringIO
except ImportError:
    from io import StringIO
import numpy as np
from shapely.geometry import MultiPoint
from argparse import RawTextHelpFormatter
from shapely.geometry import MultiPoint
from net_utils import *
from debug_utils import log_rss_memory_usage, get_script_path, timeit

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class GeoDatabase_Base(object):
	"""A base class with common methods.
	Child classes should implement _get_geodata
	"""

	def __init__(self, name, original_db_path, db_path, names=[], types={}, separator=',', index_columns=[], update=False, compression='infer', debug=False):
		"""Creates a GeoDatabase_Base object

		:param str name: Name of the table in the database.
		:param str original_db_path: Path of the csv file with the data to populate the database.
		:param str db_path: Name of the database file.
		:param list names: List of strings with the column names.
		:param dict types: Dictionary with the type of each column.
		:parm string separator: String with the separator of the csv file.
		:param index_columns: List of indices to create by using the column names.
		:param update: If True, removes the previous database file and regenerates the database.
		:param compression: If the csv file is compressed specify the kind of compression. {'infer', 'gzip', 'bz2', 'zip', 'xz', None} See https://pandas.pydata.org/pandas-docs/stable/generated/pandas.read_csv.html.
		:return: A GeoDatabase_Base object.
		"""
		self.name = name
		self.original_db_path = original_db_path
		self.db_path = db_path
		self.names = names
		self.types = types
		self.separator = separator
		self.conn = None
		self.compression = compression
		self.indices = []
		self.index_columns = index_columns
		self.update = update
		self._load_database()
		self.cursor = self.conn.cursor()
		self._results_cache = {}

	def _dict_factory(self, cursor, row):
		d = {}
		for idx, col, in enumerate(cursor.description):
			k = col[0]
			if k == 'index':
				continue
			f = self.types[k]
			if f in [int, float]:
				try:
					d[k] = f(row[idx])
				except:
					continue
			else:
				d[k] = f(row[idx])
		return d

	def _get_indices(self):
		"""Loads the list of indices in self.indices variable and returns it.
		return: A list of strings with the indices names.
		"""
		cursor = self.conn.cursor()
		cursor.execute("SELECT name FROM sqlite_master WHERE type == 'index'")
		self.indices = [index[0] for index in cursor.fetchall()]
		logger.debug('INDICES: {}'.format(self.indices))
		cursor.close()
		return self.indices

	def _create_indices(self, columns):
		"""Creates a SQL INDEX in the database for the given columns.
		:param columns: list of indices to create based on the names of column in the database.
		:return: None
		"""
		cursor = self.conn.cursor()
		for column in columns:
			if column in self.indices:
				logger.info('The sqlite geo-database index {} already exists.'.format(column))
			else:
				logger.info('Creating Index {} for column {}'.format(column, column))
				cursor.execute('CREATE INDEX {} ON {}({})'.format(column, self.name, column))
		cursor.close()

	def _load_database(self):
		"""Loads the database. If Update is true, populates the database with the self.original_db_path.
		:param update: If True, removes the previous database file and regenerates the database. Otherwise just loads the database.
		:return: SQL connection to sqlite3.
		"""
		if not (os.path.exists(self.db_path) and os.path.isfile(self.db_path)):
			self.update = True

		if self.update:
			logging.warning('Updating database {} from file {}.'.format(self.db_path, self.original_db_path))
			try:
				os.remove(self.db_path)
				logging.warning('Database {} removed.'.format(self.db_path))
			except OSError:
				logging.warning('Database {} does not exist.'.format(self.db_path))
				pass
			#LOADING DATABASE FROM FILE
			log_rss_memory_usage('Before reading csv database.')
			df = pd.read_csv(self.original_db_path, sep=self.separator, names=self.names, dtype=self.types, compression=self.compression, keep_default_na=False, na_values=['-1.#IND', '1.#QNAN', '1.#IND', '-1.#QNAN', '#N/A N/A', '#N/A', 'N/A', 'n/a', '#NA', 'NULL', 'null', 'NaN', '-NaN', 'nan', '-nan', ''], encoding='utf-8')
			df.columns = [column.lower() for column in df.columns]
			log_rss_memory_usage('After reading csv database.')
			# The original file must be in uppercase, this avoids using the following loop whichs some how uses a lot of memory
			# for column, t in df.dtypes.iteritems():
			# 	if t == np.dtype('O'):
			# 		df[column] = df[column].str.upper()
			self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
			log_rss_memory_usage('Before to_sql.')
			df.to_sql(self.name, self.conn, if_exists='replace', chunksize=1000)
			log_rss_memory_usage('After to_sql.')
			del df
			gc.collect()
			log_rss_memory_usage('After creating sql database.')
			logger.info('Database {} created.'.format(self.db_path))
		else:
			self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
			# Open database in autocommit mode by setting isolation_level to None.
			# Set journal mode to WAL.
			self.conn.execute('pragma journal_mode=wal')
			self.conn.execute('pragma cache_size = {}'.format(500*1000))
			self.conn.execute('pragma mmap_size = {}'.format(500*10**6))
			self.conn.execute('pragma synchronous = 0')
		self._get_indices()
		self._create_indices(self.index_columns)
		self.conn.row_factory = self._dict_factory
		log_rss_memory_usage('Finished _load_database function.')
		return self.conn

	# @timeit
	def get_geodata(self, *args, **kwargs):
		"""Queries the database. It checks if the value exists in the result cache and adds the value if it didn't exist before.
		:param args: mandatory args will be passed to the corresponding self._get_geodata method of each class.
		:param kwargs: optional args will be passed to the corresponding self._get_geodata method of each class.
		:return: dictionary with geographical information like (fields may change depeding on the children classses ):
		{
			 'place_name': 'MONTERREY',
			 'country_code': 'MX',
			 'country_name': 'MEXICO',
			 'location': {'lat': 25.66667, 'lon': -100.31667},
			 'region_name': 'NUEVO LEON',
			 'representative_point': {'lat': 21.210829999999998, 'lon': -100.21194},
			 'zip_code': '64830'
		}
		"""
		keyargs = tuple((tuple(arg) if type(arg) is list else arg for arg in args))
		key = keyargs + tuple(kwargs.values())
		val = self._get_result_from_cache(key)
		if val is not False:
			return val
		val = self._get_geodata(*args, **kwargs)
		if val is None:
			return None
		self._add_result_to_cache(key, val)
		return val

	def _get_geodata(self):
		"""Queries the database. Unimplemented method. See the corresponding method self._get_geodata of each class.
		:return: dictionary with geographical information like (fields may change depeding on the children classses ):
		"""
		pass

	def _get_result_from_cache(self, key):
		"""Checks if the given key exists in the cache. If so, returns the value.
		:param key: Key.
		:return: The cached value if exists or False if not.
		"""
		return self._results_cache.get(key, False)

	def _add_result_to_cache(self, key, d):
		"""Adds the key and value d to the cache dictionary.
		:param key: Key for the dictionary.
		:param d: Result to add to the cache.
		"""
		self._results_cache[key] = d

class CountryLevel_GeoDB(GeoDatabase_Base):
	"""Class child of GeoDatabase_Base.
	Maximum input resolution: (Possible columns to use): country_name or country_code.
	Maximum output resolution: Country level latitude and longitude.
	The database used is countries.csv from the file available in this repository.
	The results provide have the following format:
	{
		 'country_code': 'US',
		 'country_name': 'UNITED STATES',
		 'location': {'lat': 37.09024, 'lon': -95.712891},
		 'representative_point': {'lat': 37.09024, 'lon': -95.712891}
	}

	The information of this database uses the data from http://download.geonames.org/export/zip/ and keeps its limitations.
	"""
	names   = ['latitude', 'longitude', 'country_code', 'country_name']
	types   = {'country_code': str, 'country_name': str, 'latitude': float, 'longitude': float}
	indices = ['country_code', 'country_name']
	def __init__(self, *args, **kwargs):
		if 'index_columns' not in kwargs:
			kwargs['index_columns'] = CountryLevel_GeoDB.indices
		if 'names' not in kwargs:
			kwargs['names'] = CountryLevel_GeoDB.names
		if 'types' not in kwargs:
			kwargs['types'] = CountryLevel_GeoDB.types
		super(CountryLevel_GeoDB, self).__init__(*args, **kwargs)
		logger.debug('CountryLevel_GeoDB DB0 loaded.')

	def _get_geodata(self, column, value, multi_op='AND'):
		"""Queries the database.
		:param column: Column to query.
		:param value: Value of the given column. Provide the value in upper case.
		:return: dictionary with geographical information like:
		{
			 'country_code': 'US',
			 'country_name': 'UNITED STATES',
			 'location': {'lat': 37.09024, 'lon': -95.712891},
			 'representative_point': {'lat': 37.09024, 'lon': -95.712891}
		}
		A representative point is a kind of centroid calculated when there are many. For example, if you query with country_code: US, there are many different entries.
		The location dictionary would be populated with the first geopoint, and the rerpesentative_point would be calculated using the method representative_point of a shapely.geometry.MultiPoint
		See https://toblerity.org/shapely/manual.html#object.representative_point
		However, in this database, there should be only one possible entry for each query.
		"""
		if type(column) is str:
			self.cursor.execute('SELECT * FROM {} WHERE {} = "{}"'.format(self.name, column, value))
		elif type(column) is list and type(value) is list and len(column) == len(value):
			op = ' {} '.format(multi_op)
			query = op.join(['"{}" = "{}"'.format(e[0], e[1]) for e in zip(column, value)])
			query = 'SELECT * FROM {} WHERE {}'.format(self.name, query)
			self.cursor.execute(query)

		results = self.cursor.fetchall()
		if results is None or len(results) == 0:
			return None
		d = results[0]
		d['location'] = '{},{}'.format(results[0]['latitude'], results[0]['longitude'])
		d['representative_point'] = d['location']
		if len(results) > 1:
			geo_points = [(r['latitude'], r['longitude']) for r in results]
			geo_points = MultiPoint(geo_points)
			repr_lat, repr_lon = geo_points.representative_point().coords[0]
			d['representative_point'] = '{},{}'.format(repr_lat, repr_lon)
		del d['latitude']
		del d['longitude']
		if 'index' in d:
			del d['index']
		return d

class ZIPLevel_GeoDB(GeoDatabase_Base):
	names = ['country_code', 'zip_code', 'place_name', 'admin_name1', 'admin_code1', 'admin_name2', 'admin_code2', 'admin_name3', 'admin_code3', 'latitude', 'longitude', 'accuracy']
	types = {'country_code': str, 'zip_code': str, 'place_name': str, 'admin_name1': str, 'admin_code1': str, 'admin_name2': str, 'admin_code2': str, 'admin_name3': str, 'admin_code3': str, 'latitude': float, 'longitude': float, 'accuracy': float }
	indices = []
	def __init__(self, name, original_db_path, db_path, names=[], types=[], index_columns=[], update=False, debug=False):
		if len(names) == 0:
			names = ZIPLevel_GeoDB.names
		if len(types) == 0:
			types = ZIPLevel_GeoDB.types
		if len(index_columns) == 0:
			index_columns = ZIPLevel_GeoDB.indices
		if not ZIPLevel_GeoDB.check_FTS5_support():
			logging.error('FTS5 extension not available in your sqlite3 installation. py-sqlite3 version: {}. sqlite3 version: {}. Please, recompile or reinstall sqlite3 modules with FTS5 support.'.format(sqlite3.version, sqlite3.sqlite_version))
			sys.exit(1)
		super(ZIPLevel_GeoDB, self).__init__(name, original_db_path, db_path, names=names, types=types, index_columns=index_columns, update=update, debug=debug)
		logger.debug('ZIPLevel_GeoDB loaded.')

	@classmethod
	def check_FTS5_support(cls):
		con = sqlite3.connect(':memory:')
		cur = con.cursor()
		cur.execute('pragma compile_options;')
		available_pragmas = cur.fetchall()
		con.close()

		if ('ENABLE_FTS5',) in available_pragmas:
			return True
		else:
			return False

	def _load_database(self):
		if not (os.path.exists(self.db_path) and os.path.isfile(self.db_path)):
			self.update = True

		if self.update:
			logging.warning('Updating database {} from file {}.'.format(self.db_path, self.original_db_path))
			try:
				os.remove(self.db_path)
				logging.warning('Database {} removed.'.format(self.db_path))
			except OSError:
				logging.warning('Database {} does not exist. Creating database...'.format(self.db_path))
				pass
			#POPULATING DATABASE FROM FILE
			self.conn = sqlite3.connect(self.db_path)
			self.cursor = self.conn.cursor()
			self.cursor.execute('CREATE VIRTUAL TABLE geoinfo USING FTS5(country_code,zip_code,place_name,admin_name1,admin_code1,admin_name2,admin_code2,admin_name3,admin_code3,latitude,longitude,accuracy);')
			log_rss_memory_usage('Before populating FTS5 database.')
			with gzip.open(self.original_db_path, 'rt') as odb:
				query = odb.read()
				log_rss_memory_usage('After reading sql database from file.')
				self.cursor.execute(query)
				self.conn.commit()
				self.cursor.close()
				self.conn.close()
				log_rss_memory_usage('After closing FTS5 database.')
				del query
				gc.collect()
				logger.info('Database {} created.'.format(self.db_path))
			log_rss_memory_usage('After populating FTS5 database.')
		self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
		self.conn.row_factory = self._dict_factory
		self.conn.text_factory = str
		return self.conn

	def _get_geodata(self, column, value, multi_op='AND'):
		'''Documentation regarding FTS search: https://www.sqlite.org/fts3.html#full_text_index_queries
		A query such as:
		zipdb.get_geodata(['place_name', 'admin_name1', 'country_code'], ['MADRID', 'MADRID', 'ES'])
		should return something like:
		{	'accuracy': 4.0,
			'admin_code1': 'MD',
			'admin_code2': 'M',
			'admin_code3': '28079',
			'admin_name1': 'MADRID',
			'admin_name2': 'MADRID',
			'admin_name3': 'MADRID',
			'country_code': 'ES',
			'country_name': None,
			'location': '40.4165,-3.7026',
			'place_name': 'MADRID',
			'representative_point': '40.4165,-3.7026',
			'zip_code': '28001'
		}
		'''
		if type(value) is str and type(column) is str and column in self.names:
			query = 'SELECT * FROM {} WHERE {} MATCH "{}:{}"'.format(self.name, self.name, column.lower(), value.lower())
		elif type(value) is str and type(column) is str and column not in self.names:
			query = 'SELECT * FROM {} WHERE {} MATCH "{}"'.format(self.name, self.name, value.lower())
		elif type(column) is list and type(value) is list and len(column) == len(value):
			op = ' {} '.format(multi_op)
			query = op.join(["{}:{}".format(c.lower(), v.lower()) for c, v in zip(column, value)])
			query = 'SELECT * FROM {} WHERE {} MATCH "{}"'.format(self.name, self.name, query)
		else:
			return None

		self.cursor.execute(query)

		results = self.cursor.fetchall()
		if results is None or len(results) == 0:
			return None
		#else
		d = results[0]
		d['location'] = '{},{}'.format(results[0]['latitude'], results[0]['longitude'])
		d['representative_point'] = d['location']
		if len(results) > 1:
			geo_points = [(r['latitude'], r['longitude']) for r in results]
			geo_points = MultiPoint(geo_points)
			repr_lat, repr_lon = geo_points.representative_point().coords[0]
			d['representative_point'] = '{},{}'.format(repr_lat, repr_lon)

		del d['latitude']
		del d['longitude']
		d['country_name'] = None

		return d

class ZIP_GeoIPDB(GeoDatabase_Base):
	"""Class child of GeoDatabase_Base.
	Maximum input resolution: (Possible columns to use): country_name, country_code, region_name, place_name or an IP address.
	Maximum output resolution: ZIP level latitude and longitude.
	The database used is IP2LOCATION-LITE-DB9.CSV from ip2location gziped in the repository.
	The results provide have the following format:
	{
		 'place_name': 'MONTERREY',
		 'country_code': 'MX',
		 'country_name': 'MEXICO',
		 'location': {'lat': 25.66667, 'lon': -100.31667},
		 'region_name': 'NUEVO LEON',
		 'representative_point': {'lat': 21.210829999999998, 'lon': -100.21194},
		 'zip_code': '64830'
	}
	A representative point is a kind of centroid calculated when there are many. For example, if you query with country_code: US, there are many different entries.
	The location dictionary would be populated with the first geopoint, and the rerpesentative_point would be calculated using the method representative_point of a shapely.geometry.MultiPoint
	See https://toblerity.org/shapely/manual.html#object.representative_point
	However, we would recommend using the CountryLevel_GeoDB class if queries are formed only with country_name and/or country_code, otherwise use this class.
	"""
	names   = ["ip_from", "ip_to", "country_code", "country_name", "region_name", "place_name", "latitude", "longitude", "zip_code"]
	types   = {'ip_from': int, 'ip_to': int, 'country_code': str, 'country_name': str, 'region_name': str, 'place_name': str, 'latitude': float, 'longitude': float, 'zip_code': str}
	indices = ['ip_to', 'ip_from']
	def __init__(self, *args, **kwargs):
		if 'index_columns' not in kwargs:
			kwargs['index_columns'] = ZIP_GeoIPDB.indices
		if 'names' not in kwargs:
			kwargs['names'] = ZIP_GeoIPDB.names
		if 'types' not in kwargs:
			kwargs['types'] = ZIP_GeoIPDB.types
		if 'compression' not in kwargs:
			kwargs['compression'] = 'gzip'
		self.db_folder = kwargs.pop('db_folder', os.path.join(get_script_path(), 'db'))
		super(ZIP_GeoIPDB, self).__init__(*args, **kwargs)
		self.ip_int_to_list = self._load_cache_ip_file()
		# tempfile = StringIO()
		# self.conn.row_factory = sqlite3.Row
		# logger.info('dump...')
		# for line in self.conn.iterdump():
		# 	tempfile.write('%s\n' % line)
		# self.conn.close()
		# tempfile.seek(0)
		# logger.info('loading from memory.')
		# self.conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
		# self.conn.execute('pragma journal_mode=wal')
		# self.conn.execute('pragma cache_size = {}'.format(600*1000))
		# self.conn.execute('pragma mmap_size = {}'.format(600*10**6))
		# self.conn.execute('pragma synchronous = 0')
		# self.conn.cursor().executescript(tempfile.read())
		# self.conn.commit()
		# self.conn.row_factory = self._dict_factory
		# self.cursor = self.conn.cursor()
		logger.info('ZIP_GeoIPDB DB9 loaded.')

	def _load_cache_ip_file(self):
		#LOADING DATABASE FROM FILE
		cache_filename = os.path.join(self.db_folder, 'ip_int_to_list.npy')
		if os.path.exists(cache_filename):
			logger.info('File for cache IP_to file already exists.')
			return np.load(cache_filename)
		logger.info('Creating cache IP_to file.')
		df = pd.read_csv(self.original_db_path, sep=self.separator, names=self.names, usecols=["ip_to"], dtype=self.types, compression=self.compression, keep_default_na=False, na_values=['-1.#IND', '1.#QNAN', '1.#IND', '-1.#QNAN', '#N/A N/A', '#N/A', 'N/A', 'n/a', '#NA', 'NULL', 'null', 'NaN', '-NaN', 'nan', '-nan', ''], encoding='utf-8')
		np.save(cache_filename, df['ip_to'].values)
		return df['ip_to'].values

	# #@timeit
	# def _n_search(self, value):
	# 	idx = np.searchsorted(self.ip_int_to_list, value, side='right')
	# 	return idx, self.ip_int_to_list[idx]

	# #@timeit
	# def _nnn_search(self, value):
	# 	idx = bisect.bisect_right(self.ip_int_to_list, value)
	# 	return idx, self.ip_int_to_list[idx]

	def _get_geodata(self, column, value, multi_op='AND', str_ip=True):
		"""Queries the database.
		:param column: Column or list of columns to query.
		:param value: Value or list of values of the given column/s. Provide the value/s in upper case.
		:return: dictionary with geographical information like:
		{
			 'place_name': 'MONTERREY',
			 'country_code': 'MX',
			 'country_name': 'MEXICO',
			 'location': {'lat': 25.66667, 'lon': -100.31667},
			 'region_name': 'NUEVO LEON',
			 'representative_point': {'lat': 21.210829999999998, 'lon': -100.21194},
			 'zip_code': '64830'
		}
		"""
		if column == 'ip':
			if str_ip:
				try:
					value = ip2int(str(value))
				except Exception as e:
					logger.warning('Error in ip2int with ip: |{}|'.format(str(value)))
					return None
			idx = bisect.bisect_right(self.ip_int_to_list, value)
			ip_to = self.ip_int_to_list[idx]
			query = 'SELECT * FROM {} WHERE ip_to = "{}"'.format(self.name, ip_to)
			#query = 'SELECT * FROM {} WHERE {} BETWEEN ip_from AND ip_to'.format(self.name, value)
		elif type(column) is str:
			query = 'SELECT * FROM {} WHERE {} = "{}"'.format(self.name, column, value)
		elif type(column) is list and type(value) is list and len(column) == len(value):
			op = ' {} '.format(multi_op)
			query = op.join(['"{}" = "{}"'.format(e[0], e[1]) for e in zip(column, value)])
			query = 'SELECT * FROM {} WHERE {}'.format(self.name, query)
		else:
			return None
		logger.debug('Query: {}'.format(query))

		self.cursor.execute(query)
		results = self.cursor.fetchall()
		logger.debug('Results {}'.format(results))
		if results is None or len(results) == 0:
			return None
		d = results[0]
		d['location'] = '{},{}'.format(results[0]['latitude'], results[0]['longitude'])
		d['representative_point'] = d['location']
		if len(results) > 1:
			logger.debug('More than 1 result. Creating centroid.')
			geo_points = [(r['latitude'], r['longitude']) for r in results]
			geo_points = MultiPoint(geo_points)
			repr_lat, repr_lon = geo_points.representative_point().coords[0]
			d['representative_point'] = '{},{}'.format(repr_lat, repr_lon)
			logger.debug('Centroid done.')

		for k in ['latitude', 'longitude', 'ip_from', 'ip_to', 'index']:
			if k in d:
				del d[k]

		return d
