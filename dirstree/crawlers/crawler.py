from pathlib import Path
from typing import Any, Callable, Collection, Dict, Generator, List, Optional, Union

import pathspec
from cantok import AbstractToken, DefaultToken
from printo import descript_data_object, not_none

from dirstree.crawlers.abstract import AbstractCrawler


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

    Only the first argument with the directory path is required, the rest are optional.
    """

    def __init__(
        self,
        *paths: Union[str, Path],
        extensions: Optional[Collection[str]] = None,
        exclude: Optional[List[str]] = None,
        filter: Optional[Callable[[Path], bool]] = None,  # noqa: A002
        token: AbstractToken = DefaultToken(),  # noqa: B008
    ) -> None:
        if extensions is not None:
            for extension in extensions:
                if not extension.startswith('.'):
                    raise ValueError(
                        f'The line with the file extension must start with a dot. You have passed: "{extension}".',
                    )

        self.paths = paths
        self.extensions = extensions
        self.exclude = exclude if exclude is not None else []
        self.filter = filter
        self.token = token

        self.addictional_repr_filters: Dict[str, Callable[[Any], bool]] = {}

    def __repr__(self) -> str:
        filters={
            'extensions': not_none,
            'exclude': lambda x: bool(x),
            'filter': not_none,
            'token': lambda x: not isinstance(x, DefaultToken),
        }
        filters.update(self.addictional_repr_filters)

        return descript_data_object(
            self.__class__.__name__,
            self.paths,
            {
                'extensions': self.extensions,
                'exclude': self.exclude,
                'filter': self.filter,
                'token': self.token,
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
                        child_path.is_file()
                        and not excludes_spec.match_file(child_path)
                        and (self.extensions is None or child_path.suffix in self.extensions)
                        and (self.filter is None or self.filter(child_path))
                    ):
                        yield child_path

                    if not token:
                        break
            else:
                break
