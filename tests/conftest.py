import os
from pathlib import Path
from typing import Tuple, Type, Union

import pytest
from cantok import CancellationError, SimpleToken


def extract_cancellation_message(token_class: Type[SimpleToken]) -> str:
    """Instantiate a cancelled token of ``token_class`` and return the message that
    cantok raises from ``.check()``.

    ``token_class`` must accept ``cancelled=True`` as a constructor argument (the
    canonical example is ``SimpleToken``). The trailing ``raise AssertionError``
    is a contract assertion: cantok's API guarantees that ``.check()`` on a
    cancelled token raises, so the only way we ever reach it is a cantok-side
    contract violation.
    """
    try:
        token_class(cancelled=True).check()
    except CancellationError as original_exception:
        return str(original_exception)
    raise AssertionError('cantok contract violation: .check() on a cancelled token must raise')


def predict_raised_exception(
    raise_on_cancel_value: Union[bool, BaseException, Type[BaseException]],
    native_message: str,
) -> Tuple[Type[BaseException], str]:
    """Return ``(expected_type, expected_message)`` for a given ``raise_on_cancel`` form.

    Maps each of the three truthy flag forms to what the iteration is expected
    to raise when the cancellation fires:

    - ``True`` → cantok ``CancellationError`` with cantok's native message;
    - instance → that instance's type with its own message (``str(instance)``);
    - class → that class with cantok's native message (the constructor is
      called with ``str(original_exception)``).

    ``False`` accepts the type only for caller convenience (parametrize lists
    often share a wider ``bool`` type), but passing it is a programming error:
    the function is meaningful only when a raise is expected, so ``False`` hits
    the trailing assertion. ``native_message`` is the message cantok would emit
    for the token used in the test (typically
    ``extract_cancellation_message(SimpleToken)`` for pre-cancelled SimpleToken
    scenarios, or extracted inline from the actual token for mid-iteration
    scenarios).
    """
    if raise_on_cancel_value is True:
        return CancellationError, native_message
    if isinstance(raise_on_cancel_value, type):
        return raise_on_cancel_value, native_message
    if isinstance(raise_on_cancel_value, BaseException):
        return type(raise_on_cancel_value), str(raise_on_cancel_value)
    raise AssertionError(f'predict_raised_exception is not meaningful for {raise_on_cancel_value!r}')


@pytest.fixture(params=[str, Path])
def crawl_directory_path(request):
    return request.param(os.path.join('tests', 'test_files', 'walk_it'))


@pytest.fixture(params=[str, Path])
def second_crawl_directory_path(request):
    return request.param(os.path.join('tests', 'test_files', 'walk_it_2'))


@pytest.fixture(params=[str, Path])
def all_entities_directory_path(request):
    return request.param(os.path.join('tests', 'test_files', 'all_entities'))
