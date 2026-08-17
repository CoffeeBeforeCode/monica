"""
WEF Scraper — World Economic Forum Emerging Technologies

This module scrapes the WEF Emerging Technologies page because WEF doesn't provide
an RSS feed. It uses BeautifulSoup to parse HTML, extract articles, and filter by
keywords to reduce noise.

WHY web scraping: WEF is a key source for emerging technology trends, but they don't
publish an RSS feed. Web scraping is the only way to access their content programmatically.

WHY keyword filtering: WEF covers many topics. We filter to technology-related articles
(AI, quantum, cybersecurity, renewable energy, blockchain, etc.) to keep the feed
signal-to-noise ratio high and aligned with Monica's focus.
"""

import requests
import logging
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Dict, Optional
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


class WEFScraper:
    """
    Scrapes and parses the WEF Emerging Technologies page.
    """
    
    # Keyword list from spec (case-insensitive matching)
    KEYWORDS = [
        "artificial intelligence", "ai", "machine learning", "claude", "gpt", "llm",
        "quantum computing", "quantum",
        "cybersecurity", "cyber security", "security", "breach", "vulnerability",
        "emerging technologies", "emerging technology",
        "infrastructure", "critical infrastructure",
        "renewable energy", "clean energy", "energy transition", "net zero",
        "blockchain", "cryptocurrency", "crypto",
        "5g", "6g", "telecommunications",
        "biotechnology", "biotech", "genetic", "crispr"
    ]
    
    def __init__(self, timeout: int = 10, retry_attempts: int = 3):
        """
        Initialise the WEF scraper.
        
        WHY timeout: Web scraping can be slower than RSS fetching because we're
        parsing full HTML. 10 seconds is still reasonable for the Azure Function
        90-second execution window.
        """
        self.url = "https://agenda.weforum.org/stories/emerging-technologies/"
        self.timeout = timeout
        self.retry_attempts = retry_attempts
    
    def scrape_and_filter(self) -> List[Dict]:
        """
        Scrape the WEF page and return articles matching keyword filter.
        
        Returns:
            List of article dictionaries (same format as RSS parser)
        
        WHY combined scrape+filter: We filter during scraping to avoid unnecessary
        processing of non-relevant articles and reduce Teams API calls.
        """
        
        articles = []
        last_error = None
        
        for attempt in range(1, self.retry_attempts + 1):
            try:
                logger.info(f"Scraping WEF page (attempt {attempt}/{self.retry_attempts})...")
                
                # Fetch page
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                
                response = requests.get(
                    self.url,
                    headers=headers,
                    timeout=self.timeout
                )
                response.raise_for_status()
                
                # Parse HTML
                soup = BeautifulSoup(response.content, 'lxml')
                
                # Find article containers
                # WHY try multiple selectors: If the WEF page structure changes,
                # we try fallback selectors. Common patterns: <article>, div.story, div.article-card
                articles_html = soup.find_all('article')
                if not articles_html:
                    logger.warning("No <article> tags found, trying alternative selectors...")
                    articles_html = soup.find_all('div', class_='story')
                if not articles_html:
                    articles_html = soup.find_all('div', class_='article-card')
                
                if not articles_html:
                    logger.warning("No article containers found on WEF page. Page structure may have changed.")
                    return []
                
                logger.info(f"Found {len(articles_html)} article containers on WEF page")
                
                # Extract and filter articles
                for article_elem in articles_html:
                    article = self._extract_article(article_elem)
                    
                    if not article:
                        continue
                    
                    # Apply keyword filter
                    if self._matches_keyword_filter(article['headline'], article['summary']):
                        articles.append(article)
                    else:
                        logger.debug(f"Filtered out: {article['headline'][:60]}")
                
                logger.info(f"Extracted {len(articles)} articles from WEF (after keyword filtering)")
                return articles
                
            except requests.RequestException as e:
                last_error = e
                logger.warning(f"Attempt {attempt} failed to fetch WEF: {e}")
                if attempt < self.retry_attempts:
                    import time
                    logger.info(f"Retrying in {attempt}s...")
                    time.sleep(attempt)
            
            except Exception as e:
                logger.error(f"Error scraping WEF: {e}", exc_info=True)
                last_error = e
                break
        
        logger.error(f"Failed to scrape WEF after {self.retry_attempts} attempts: {last_error}")
        return []
    
    def _extract_article(self, article_elem) -> Optional[Dict]:
        """
        Extract article metadata from a single HTML article container.
        
        WHY separate method: Keeps scraping logic modular. Different HTML structures
        might require selector adjustments in the future.
        
        Returns:
            Article dictionary or None if required fields missing
        """
        
        try:
            # Extract headline from <h2> or <h3> tags
            headline_elem = article_elem.find('h2') or article_elem.find('h3')
            headline = headline_elem.get_text(strip=True) if headline_elem else None
            
            if not headline:
                return None
            
            # Truncate to 250 chars
            if len(headline) > 250:
                headline = headline[:247] + '...'
            
            # Extract link
            link_elem = article_elem.find('a', href=True)
            link = link_elem.get('href', '').strip() if link_elem else None
            
            if not link:
                logger.warning(f"Skipping article with no link: {headline[:50]}")
                return None
            
            # Convert relative URLs to absolute
            # WHY: WEF might use relative paths. We need full URLs for the Teams card.
            if link and not link.startswith('http'):
                link = urljoin('https://agenda.weforum.org', link)
            
            # Extract summary from first <p> tag
            summary_elem = article_elem.find('p')
            summary = summary_elem.get_text(strip=True)[:200] if summary_elem else ''
            
            # Extract featured image
            image_url = self._extract_image(article_elem)
            
            # Extract publish date
            publish_date = self._extract_date(article_elem)
            
            return {
                'headline': headline,
                'summary': summary,
                'link': link,
                'image_url': image_url,
                'publish_date': publish_date,
                'source': 'World Economic Forum'
            }
        
        except Exception as e:
            logger.warning(f"Error extracting WEF article: {e}")
            return None
    
    def _extract_image(self, article_elem) -> Optional[str]:
        """
        Extract featured image URL from article HTML.
        
        WHY multiple approaches: Images might be in <img src>, <img srcset>,
        or background-image CSS. We try the most common patterns first.
        """
        
        # Try <img> tag with src attribute
        img_elem = article_elem.find('img')
        if img_elem:
            # Try src first
            src = img_elem.get('src', '').strip()
            if src and src.startswith('http'):
                return src
            
            # Try data-src (lazy loading)
            data_src = img_elem.get('data-src', '').strip()
            if data_src and data_src.startswith('http'):
                return data_src
            
            # Try srcset (responsive images)
            srcset = img_elem.get('srcset', '').strip()
            if srcset:
                # Extract first URL from srcset
                first_url = srcset.split()[0].strip(',')
                if first_url.startswith('http'):
                    return first_url
        
        # Try to find image in picture element (HTML5 responsive)
        picture = article_elem.find('picture')
        if picture:
            sources = picture.find_all('source')
            for source in sources:
                srcset = source.get('srcset', '').strip()
                if srcset:
                    first_url = srcset.split()[0].strip(',')
                    if first_url.startswith('http'):
                        return first_url
        
        return None
    
    def _extract_date(self, article_elem) -> str:
        """
        Extract publish date from article HTML.
        
        WHY: Some articles might not have dates. We default to today's date
        to ensure all cards have a date field.
        """
        
        try:
            # Look for <time> tag
            time_elem = article_elem.find('time')
            if time_elem:
                date_str = time_elem.get('datetime') or time_elem.get_text(strip=True)
                if date_str:
                    from dateutil import parser as date_parser
                    dt = date_parser.parse(date_str)
                    return dt.strftime("%d %b %Y")
            
            # Look for date class
            date_elem = article_elem.find('span', class_='date')
            if date_elem:
                date_str = date_elem.get_text(strip=True)
                from dateutil import parser as date_parser
                dt = date_parser.parse(date_str)
                return dt.strftime("%d %b %Y")
            
            # Default to today
            return datetime.now().strftime("%d %b %Y")
        
        except Exception as e:
            logger.warning(f"Error parsing WEF date: {e}. Using today.")
            return datetime.now().strftime("%d %b %Y")
    
    def _matches_keyword_filter(self, headline: str, summary: str) -> bool:
        """
        Check if article matches at least one keyword.
        
        WHY case-insensitive: Users might search for "AI" or "ai" or "Ai".
        We normalise to lowercase for matching.
        
        WHY "at least one": Articles rarely hit multiple keywords. One match
        is sufficient to include the article.
        
        Returns:
            True if article contains at least one keyword, False otherwise
        """
        
        combined_text = (headline + ' ' + summary).lower()
        return any(keyword in combined_text for keyword in self.KEYWORDS)
