from inspect import signature

from dirstree import Crawler, PythonCrawler


def test_signature_of_python_crawler_is_signature_of_crawler_without_extensions():
    crawler_parameters = list(signature(Crawler).parameters.keys())
    crawler_parameters.remove('extensions')

    assert crawler_parameters == list(signature(PythonCrawler).parameters.keys())
