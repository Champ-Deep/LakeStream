from src.services.crawler import CrawlerService


class TestCrawlerConcurrency:
    def test_different_domains_get_independent_semaphores(self):
        crawler = CrawlerService(max_per_domain=1)

        sem_a = crawler._get_semaphore("domain-a.com")
        sem_b = crawler._get_semaphore("domain-b.com")

        assert sem_a is not sem_b

    def test_get_semaphore_creates_new_for_new_domain(self):
        crawler = CrawlerService(max_per_domain=2)

        sem1 = crawler._get_semaphore("test.com")
        sem2 = crawler._get_semaphore("test.com")

        assert sem1 is sem2

    def test_max_per_domain_config(self):
        crawler = CrawlerService(max_per_domain=3)
        assert crawler.max_per_domain == 3

    def test_default_max_per_domain(self):
        crawler = CrawlerService()
        assert crawler.max_per_domain == 2
