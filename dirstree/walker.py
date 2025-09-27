from typing import List, Optional, Union, Collection, Generator
from pathlib import Path

import pathspec


class DirectoryWalker:
    def __init__(self, path: Union[str, Path], extensions: Collection[str] = ('.py',), exclude_patterns: Optional[List[str]] = None) -> None:
        self.path = path
        self.extensions = extensions
        self.exclude_patterns = exclude_patterns if exclude_patterns is not None else []

    def walk(self) -> Generator[Path, None, None]:
        base_path = Path(self.path)
        excludes_spec = pathspec.PathSpec.from_lines('gitwildmatch', self.exclude_patterns)

        for child_path in base_path.rglob('*'):
            if child_path.is_file() and child_path.suffix in self.extensions and not excludes_spec.match_file(child_path):
                yield child_path
