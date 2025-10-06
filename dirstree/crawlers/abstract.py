from abc import ABC, abstractmethod
from typing import Generator
from pathlib import Path

from cantok import AbstractToken, DefaultToken


class AbstractCrawler(ABC):
    def __iter__(self) -> Generator[Path, None, None]:
        yield from self.go()

    def __add__(self, other: 'AbstractCrawler') -> 'AbstractCrawler':
        if not isinstance(other, AbstractCrawler):
            raise TypeError(f"Cannot add {type(self)} and {type(other)}.")

        from dirstree.crawlers.group import CrawlerGroup

        return CrawlerGroup([self, other])

    @abstractmethod
    def go(self, token: AbstractToken = DefaultToken()) -> Generator[Path, None, None]:
        pass
