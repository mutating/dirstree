from pathlib import Path
from typing import Any, Callable, Collection, Dict, Generator, List, Optional, Union

import pathspec
from cantok import AbstractToken, DefaultToken
from printo import describe_data_object, not_none
from sigmatch import PossibleCallMatcher

from dirstree.crawlers.abstract import AbstractCrawler
from dirstree.errors import IncompatibleCrawlerOptionsError


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

    def __init__(
        self,
        *paths: Union[str, Path],
        extensions: Optional[Collection[str]] = None,
        exclude: Optional[List[str]] = None,
        filter: Optional[Callable[[Path], bool]] = None,  # noqa: A002
        token: AbstractToken = DefaultToken(),  # noqa: B008
        only_files: bool = True,
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

        self.paths = paths
        self.extensions = extensions
        self.exclude = exclude if exclude is not None else []
        self.filter = filter
        self.token = token
        self.only_files = only_files

        self.addictional_repr_filters: Dict[str, Callable[[Any], bool]] = {}

    def __repr__(self) -> str:
        filters={
            'extensions': not_none,
            'exclude': lambda x: bool(x),
            'filter': not_none,
            'token': lambda x: not isinstance(x, DefaultToken),
            'only_files': lambda x: x is False,
        }
        filters.update(self.addictional_repr_filters)

        return describe_data_object(
            self.__class__.__name__,
            self.paths,
            {
                'extensions': self.extensions,
                'exclude': self.exclude,
                'filter': self.filter,
                'token': self.token,
                'only_files': self.only_files,
            },
            filters=filters,  # type: ignore[arg-type]
        )

    def go(self, token: AbstractToken = DefaultToken()) -> Generator[Path, None, None]:  # noqa: B008
        token = token + self.token

        excludes_spec = pathspec.PathSpec.from_lines('gitwildmatch', self.exclude)

        for path in self.paths:
            base_path = Path(path)
            if token:
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

                    if not token:
                        break
            else:
                break
