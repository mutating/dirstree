from pathlib import Path
from typing import (
    Any,
    Callable,
    Collection,
    Dict,
    Generator,
    List,
    Optional,
    Type,
    Union,
)

import pathspec
from cantok import AbstractToken, CancellationError, DefaultToken
from printo import describe_data_object, not_none
from sigmatch import PossibleCallMatcher
from sigmatch.errors import SignatureMismatchError, SignatureNotFoundError

from dirstree.crawlers.abstract import AbstractCrawler
from dirstree.errors import IncompatibleCrawlerOptionsError


def _exception_class_accepts_single_positional(cls: type) -> bool:
    try:
        PossibleCallMatcher('.').match(cls, raise_exception=True)
    except SignatureNotFoundError:
        return True
    except SignatureMismatchError:
        return False
    return True


# TODO: add typing tests
class Crawler(AbstractCrawler):
    """
    The crawler is used to sort through all the files in some directory. If necessary, you can specify filters, that is, certain conditions under which some files will be ignored.

    A simple example of the code:

    >>> from dirstree import Crawler
    >>>
    >>> crawler = Crawler('path/to/directory', extensions=['.py', '.txt'], exclude=['*.tmp'])
    >>>
    >>> for file in crawler:
    >>>     print(file)

    Or, if you just want to run a function on each path the crawler would yield, use apply():

    >>> Crawler('path/to/directory', extensions=['.py']).apply(print)

    Only the first argument with the directory path is required, the rest are optional.
    """

    def __init__(  # noqa: PLR0913
        self,
        *paths: Union[str, Path],
        extensions: Optional[Collection[str]] = None,
        exclude: Optional[List[str]] = None,
        filter: Optional[Callable[[Path], bool]] = None,  # noqa: A002
        token: AbstractToken = DefaultToken(),  # noqa: B008
        only_files: bool = True,
        freeze: bool = False,
        raise_on_cancel: Union[bool, BaseException, Type[BaseException]] = False,
    ) -> None:
        if extensions is not None and not only_files:
            raise IncompatibleCrawlerOptionsError(
                'The "extensions" and "only_files=False" options are incompatible: '
                'extensions can be applied only when the crawler yields files, '
                'because non-file filesystem entities do not have meaningful file extensions.',
            )
        if extensions is not None:
            for extension in extensions:
                if not extension.startswith('.'):
                    raise ValueError(
                        f'The line with the file extension must start with a dot. You have passed: "{extension}".',
                    )
        if filter is not None:
            PossibleCallMatcher('.').match(filter, raise_exception=True)

        if not (
            isinstance(raise_on_cancel, (bool, BaseException))
            or (
                isinstance(raise_on_cancel, type)
                and issubclass(raise_on_cancel, BaseException)
                and _exception_class_accepts_single_positional(raise_on_cancel)
            )
        ):
            raise TypeError(
                'raise_on_cancel must be a bool, a BaseException instance, '
                'or a BaseException subclass whose constructor accepts a single positional argument.',
            )

        self.paths = paths
        self.extensions = extensions
        self.exclude = exclude if exclude is not None else []
        self.filter = filter
        self.token = token
        self.only_files = only_files
        self.frozen = freeze

        if isinstance(raise_on_cancel, bool):
            self.raise_on_cancel: bool = raise_on_cancel
            self.cancellation_exception: Optional[Union[BaseException, Type[BaseException]]] = None
        else:
            self.raise_on_cancel = True
            self.cancellation_exception = raise_on_cancel

        self.addictional_repr_filters: Dict[str, Callable[[Any], bool]] = {}

    def __repr__(self) -> str:
        filters={
            'extensions': not_none,
            'exclude': lambda x: bool(x),
            'filter': not_none,
            'token': lambda x: not isinstance(x, DefaultToken),
            'only_files': lambda x: x is False,
            'freeze': lambda x: x is True,
            'raise_on_cancel': lambda x: x is not False,
        }
        filters.update(self.addictional_repr_filters)

        displayed_raise_on_cancel: Union[bool, BaseException, Type[BaseException]] = (
            self.cancellation_exception if self.cancellation_exception is not None else self.raise_on_cancel
        )

        return describe_data_object(
            self.__class__.__name__,
            self.paths,
            {
                'extensions': self.extensions,
                'exclude': self.exclude,
                'filter': self.filter,
                'token': self.token,
                'only_files': self.only_files,
                'freeze': self.frozen,
                'raise_on_cancel': displayed_raise_on_cancel,
            },
            filters=filters,  # type: ignore[arg-type]
        )

    def _check_token(self, token: AbstractToken) -> bool:
        if token:
            return True
        if self.raise_on_cancel:
            try:
                token.check()
            except CancellationError as original_exception:
                if self.cancellation_exception is None:
                    raise
                if isinstance(self.cancellation_exception, type):
                    raise self.cancellation_exception(str(original_exception)) from original_exception
                raise self.cancellation_exception from original_exception
        return False

    def _traverse(self, token: AbstractToken) -> Generator[Path, None, None]:
        excludes_spec = pathspec.PathSpec.from_lines('gitwildmatch', self.exclude)

        for path in self.paths:
            if not self._check_token(token):
                return
            base_path = Path(path)
            for child_path in base_path.rglob('*'):
                if (
                    (not self.only_files or child_path.is_file())
                    and not (
                        excludes_spec.match_file(child_path)
                        or (child_path.is_dir() and excludes_spec.match_file(f'{child_path}/'))
                    )
                    and (self.extensions is None or child_path.suffix in self.extensions)
                    and (self.filter is None or self.filter(child_path))
                ):
                    yield child_path

                if not self._check_token(token):
                    return
        self._check_token(token)

    def go(self, token: AbstractToken = DefaultToken()) -> Generator[Path, None, None]:  # noqa: B008
        instance_token = self.token
        token = token + instance_token

        if self.frozen:
            snapshot = list(self._traverse(token))
            for path in snapshot:
                if not self._check_token(token):
                    return
                yield path
        else:
            yield from self._traverse(token)
