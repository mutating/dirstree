from typing import List, Optional, Union, Collection, Generator, Callable
from pathlib import Path

import pathspec
from printo import descript_data_object
from cantok import AbstractToken, DefaultToken


# TODO: add a special class to crawl only throw python files
# TODO: add typing tests
# TODO: add an exception if an extension is not starting from a dot
class Crawler:
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
        path: Union[str, Path],
        extensions: Optional[Collection[str]] = None,
        exclude: Optional[List[str]] = None,
        filter: Callable[[Path], bool] = lambda x: True,
        token: AbstractToken = DefaultToken(),
    ) -> None:
        if extensions is not None:
            for extension in extensions:
                if not extension.startswith('.'):
                    raise ValueError(
                        f'The line with the file extension must start with a dot. You have transmitted: "{extension}".'
                    )

        self.path = path
        self.extensions = extensions
        self.exclude = exclude if exclude is not None else []
        self.filter = filter
        self.token = token

    def __repr__(self) -> str:
        addictions = {}
        if self.extensions is not None:
            addictions['extensions'] = self.extensions
        if self.exclude:
            addictions['exclude'] = self.exclude

        return descript_data_object(self.__class__.__name__, (self.path,), addictions)

    def __iter__(self) -> Generator[Path, None, None]:
        yield from self.go()

    def go(self, token: AbstractToken = DefaultToken()) -> Generator[Path, None, None]:
        token = token + self.token
        base_path = Path(self.path)
        excludes_spec = pathspec.PathSpec.from_lines('gitwildmatch', self.exclude)

        if token:
            for child_path in base_path.rglob('*'):
                if (
                    child_path.is_file()
                    and not excludes_spec.match_file(child_path)
                    and self.filter(child_path)
                ):
                    if self.extensions is None or child_path.suffix in self.extensions:
                        yield child_path

                if not token:
                    break
