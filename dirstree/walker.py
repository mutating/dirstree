from typing import List, Optional, Union, Collection, Generator
from pathlib import Path

import pathspec


# TODO: rename this class to "Crawler"
# TODO: add docstring
# TODO: add a .__repr__() method
# TODO: add possibility to iterate throw an object without using the .walk() method
# TODO: add a special class to crawl only throw python files
# TODO: add typing tests
class DirectoryWalker:
    def __init__(self, path: Union[str, Path], extensions: Optional[Collection[str]] = None, exclude_patterns: Optional[List[str]] = None) -> None:
        self.path = path
        self.extensions = extensions
        self.exclude_patterns = exclude_patterns if exclude_patterns is not None else []  # TODO: rename it to just "exclude"

    def walk(self) -> Generator[Path, None, None]:
        base_path = Path(self.path)
        excludes_spec = pathspec.PathSpec.from_lines('gitwildmatch', self.exclude_patterns)

        for child_path in base_path.rglob('*'):
            if child_path.is_file() and not excludes_spec.match_file(child_path):
                if self.extensions is None or child_path.suffix in self.extensions:
                    yield child_path
