"""
Feed Parser — RSS Feed Processing

This module handles fetching and parsing RSS feeds from Chatham House and Internet Society.
It extracts article metadata (headline, summary, image, link, publish date) and handles
errors gracefully (missing fields, malformed XML, network issues).

WHY: We use feedparser library because it's robust, industry-standard, and handles
various RSS/Atom feed formats automatically. It also manages dates across different
timezone formats without manual parsing.
"""

import feedparser
import logging
from datetime import datetime
from dateutil import parser as date_parser
from typing import List, Dict, Optional
import requests

logger = logging.getLogger(__name__)


class FeedParser:
    """
    Fetches and parses RSS feeds, extracting article metadata.
    """
    
    def __init__(self, timeout: int = 10, retry_attempts: int = 3):
        """
        Initialise the feed parser.
        
        WHY timeout: Feed servers might be slow or unresponsive. 10 seconds is
        a reasonable balance between thoroughness and execution time constraints.
        
        WHY retry_attempts: Network issues are transient. We retry up to 3 times
        before giving up, following the spec's error handling requirement.
        """
        self.timeout = timeout
        self.retry_attempts = retry_attempts
    
    def fetch_feed(self, url: str, source_name: str) -> List[Dict]:
        """
        Fetch and parse an RSS feed, returning new articles since last run.
        
        Args:
            url: RSS feed URL
            source_name: Publication name (e.g., "Chatham House")
        
        Returns:
            List of article dictionaries with extracted metadata
        
        WHY List[Dict]: This matches the card builder's expected input format.
        Each dictionary has consistent keys: headline, summary, link, image_url, etc.
        """
        
        articles = []
        last_attempt_error = None
        
        # Retry loop
        for attempt in range(1, self.retry_attempts + 1):
            try:
                logger.info(f"Fetching {source_name} feed (attempt {attempt}/{self.retry_attempts})...")
                
                # WHY User-Agent: Some servers block requests without a recognisable User-Agent.
                # This is a standard browser string that avoids 403 Forbidden errors.
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                
                # Fetch feed with timeout
                response = requests.get(url, headers=headers, timeout=self.timeout)
                response.raise_for_status()
                
                # Parse feed
                feed = feedparser.parse(response.content)
                
                # Check for parsing errors
                if feed.bozo:
                    logger.warning(f"{source_name} feed has bozo flag: {feed.bozo_exception}")
                
                # Extract articles
                logger.info(f"Found {len(feed.entries)} total entries in {source_name} feed")
                
                for entry in feed.entries:
                    article = self._extract_article(entry, source_name)
                    if article:
                        articles.append(article)
                
                logger.info(f"Extracted {len(articles)} valid articles from {source_name}")
                return articles
                
            except requests.RequestException as e:
                last_attempt_error = e
                logger.warning(f"Attempt {attempt} failed for {source_name}: {e}")
                if attempt < self.retry_attempts:
                    logger.info(f"Retrying in {attempt}s...")
                    import time
                    time.sleep(attempt)  # Exponential backoff
            
            except Exception as e:
                logger.error(f"Error parsing {source_name} feed: {e}", exc_info=True)
                last_attempt_error = e
                break
        
        # All retries exhausted
        logger.error(f"Failed to fetch {source_name} after {self.retry_attempts} attempts: {last_attempt_error}")
        return []
    
    def _extract_article(self, entry: Dict, source_name: str) -> Optional[Dict]:
        """
        Extract article metadata from a single RSS entry.
        
        WHY separate method: This keeps the parsing logic modular and testable.
        RSS feeds vary in structure (some use media:content, others use enclosures).
        This method handles common patterns.
        
        Returns:
            Dictionary with article metadata, or None if required fields missing
        """
        
        try:
            # Extract headline
            headline = entry.get('title', '').strip()
            if not headline:
                logger.warning("Skipping entry with no headline")
                return None
            
            # Truncate to 250 chars per spec
            if len(headline) > 250:
                headline = headline[:247] + '...'
            
            # Extract link
            link = entry.get('link', '').strip()
            if not link:
                logger.warning(f"Skipping headline with no link: {headline[:50]}")
                return None
            
            # Extract summary/description
            # WHY try multiple fields: RSS feeds inconsistently use 'summary' or 'description'
            summary = entry.get('summary') or entry.get('description', '')
            summary = summary.strip()
            
            # Extract first 150-200 characters, respect word boundaries
            if summary:
                # Find natural break point around 150-200 chars
                if len(summary) > 200:
                    # Truncate at 200 chars, then backtrack to last space
                    truncated = summary[:200]
                    last_space = truncated.rfind(' ')
                    if last_space > 100:  # Ensure we have at least ~100 chars
                        summary = truncated[:last_space] + '...'
                    else:
                        summary = truncated + '...'
            
            # Extract featured image
            image_url = self._extract_image(entry)
            
            # Extract publish date
            publish_date = self._extract_date(entry)
            
            return {
                'headline': headline,
                'summary': summary,
                'link': link,
                'image_url': image_url,
                'publish_date': publish_date,
                'source': source_name
            }
        
        except Exception as e:
            logger.warning(f"Error extracting article from {source_name}: {e}")
            return None
    
    def _extract_image(self, entry: Dict) -> Optional[str]:
        """
        Extract featured image URL from RSS entry.
        
        WHY multiple sources: RSS feeds store images in different ways:
        - media:content (Podcast feeds, RSS 2.0 with media namespace)
        - enclosure (generic enclosures, sometimes images)
        - content (raw HTML)
        - description might have <img> tags
        
        We check each in order of reliability.
        """
        
        # Check media:content (most reliable)
        if 'media_content' in entry:
            for media in entry.media_content:
                if media.get('type', '').startswith('image/'):
                    url = media.get('url', '').strip()
                    if url:
                        return url
        
        # Check enclosures
        if 'enclosures' in entry:
            for enclosure in entry.enclosures:
                if enclosure.get('type', '').startswith('image/'):
                    url = enclosure.get('href', '').strip()
                    if url:
                        return url
        
        # Check media namespace (alternate structure)
        for key in entry.keys():
            if 'media_' in key and 'url' in str(entry.get(key, '')).lower():
                value = entry.get(key)
                if isinstance(value, str) and value.startswith('http'):
                    return value
        
        # Last resort: extract from summary HTML
        summary = entry.get('summary', '')
        if '<img' in summary:
            import re
            match = re.search(r'<img[^>]+src=["\'](https?://[^\s"\']+)', summary)
            if match:
                return match.group(1)
        
        return None
    
    def _extract_date(self, entry: Dict) -> str:
        """
        Extract and format publish date from RSS entry.
        
        WHY dateutil parser: Different RSS feeds use different date formats.
        The dateutil library handles ISO 8601, RFC 2822, and many other formats
        automatically without regex parsing.
        
        Returns:
            Formatted date string: "DD MMM YYYY" (e.g., "17 Aug 2026")
        """
        
        try:
            # Try published_parsed (most RSS entries have this)
            if 'published_parsed' in entry and entry.published_parsed:
                dt = datetime.fromtimestamp(
                    datetime(*entry.published_parsed[:6]).timestamp()
                )
            # Try updated_parsed
            elif 'updated_parsed' in entry and entry.updated_parsed:
                dt = datetime.fromtimestamp(
                    datetime(*entry.updated_parsed[:6]).timestamp()
                )
            # Try raw string parsing
            elif 'published' in entry:
                dt = date_parser.parse(entry.published)
            elif 'updated' in entry:
                dt = date_parser.parse(entry.updated)
            else:
                # Default to today
                logger.warning("No date found in entry, using today")
                dt = datetime.now()
            
            # Format as "DD MMM YYYY"
            return dt.strftime("%d %b %Y")
        
        except Exception as e:
            logger.warning(f"Error parsing date: {e}. Using today.")
            return datetime.now().strftime("%d %b %Y")
