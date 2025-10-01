from typing import List, Optional, Union, Collection, Generator
from pathlib import Path

import pathspec
from printo import descript_data_object


# TODO: add possibility to iterate throw an object without using the .go() method
# TODO: add a special class to crawl only throw python files
# TODO: add typing tests
# TODO: add an exception if an extension is not starting from a dot
# # TODO: add docstring
class Crawler:
    def __init__(self, path: Union[str, Path], extensions: Optional[Collection[str]] = None, exclude: Optional[List[str]] = None) -> None:
        self.path = path
        self.extensions = extensions
        self.exclude = exclude if exclude is not None else []

    def __repr__(self) -> str:
        addictions = {}
        if self.extensions is not None:
            addictions['extensions'] = self.extensions
        if self.exclude:
            addictions['exclude'] = self.exclude

        return descript_data_object(self.__class__.__name__, (self.path,), addictions)

    def __iter__(self) -> Generator[Path, None, None]:
        yield from self.go()

    def go(self) -> Generator[Path, None, None]:
        base_path = Path(self.path)
        excludes_spec = pathspec.PathSpec.from_lines('gitwildmatch', self.exclude)

        for child_path in base_path.rglob('*'):
            if child_path.is_file() and not excludes_spec.match_file(child_path):
                if self.extensions is None or child_path.suffix in self.extensions:
                    yield child_path
