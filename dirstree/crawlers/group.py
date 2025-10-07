from typing import List, Generator
from pathlib import Path

from cantok import AbstractToken, DefaultToken
from printo import descript_data_object

from dirstree.crawlers.abstract import AbstractCrawler


class CrawlersGroup(AbstractCrawler):
    def __init__(self, crawlers: List[AbstractCrawler]) -> None:
        self.crawlers = crawlers

    def __repr__(self):
        return descript_data_object(
            type(self).__name__,
            (self.crawlers,),
            {},
        )

    def go(self, token: AbstractToken = DefaultToken()) -> Generator[Path, None, None]:
        memory = set()

        for crawler in self.crawlers:
            for path in crawler.go(token):
                if path not in memory:
                    memory.add(path)
                    yield path
