from couchbase import CouchbaseTest


class MemcachedWatsonTest(CouchbaseTest):
    pass

def create_instance():
    return MemcachedWatsonTest()
