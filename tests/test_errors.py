from dirstree import IncompatibleCrawlerOptionsError, errors


def test_errors_are_publicly_importable():
    """
    The new incompatible-options error should be public while keeping its base class.

    The test imports the concrete error from the package root, imports both error
    classes from `dirstree.errors`, and verifies identity/subclass relationships.
    """
    assert IncompatibleCrawlerOptionsError is errors.IncompatibleCrawlerOptionsError
    assert issubclass(IncompatibleCrawlerOptionsError, errors.DirstreeError)
