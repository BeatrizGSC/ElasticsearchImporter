import os, psutil, logging, platform, resource

def log_rss_memory_usage(msg=''):
	max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1e6

	rss = psutil.Process(os.getpid()).memory_info().rss
	thislog = logging.getLogger(__name__)
	thislog.info('Memory usage: {:.2f} MB. (Peak so far: {:.2f} MB) {}'.format(rss/1e6, max_rss, msg))

def get_memory_status():
	return psutil.virtual_memory()

def get_available_memory():
	return psutil.virtual_memory().available/1e6