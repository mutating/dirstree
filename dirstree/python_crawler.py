from typing import List, Optional, Union, Callable
from pathlib import Path

from cantok import AbstractToken, DefaultToken

from dirstree.crawler import Crawler


class PythonCrawler(Crawler):
    def __init__(
        self,
        path: Union[str, Path],
        exclude: Optional[List[str]] = None,
        filter: Callable[[Path], bool] = lambda x: True,
        token: AbstractToken = DefaultToken(),
    ) -> None:
        super().__init__(
            path, extensions=('.py',), exclude=exclude, filter=filter, token=token
        )
