"""
State Manager — Duplicate Detection and Execution Logging

This module manages persistent state between function executions:
1. Stores article URLs that have been posted (duplicate prevention)
2. Maintains execution logs for auditing and troubleshooting

WHY persistent state: Articles might appear in multiple feeds or re-surface in
a feed after several weeks. Without duplicate tracking, we'd post the same
article to Teams multiple times, annoying readers. The spec recommends OneDrive
storage for simplicity and audit trail visibility.

WHY OneDrive: It's accessible from the Azure Function, creates a visible audit
trail (you can view the log files anytime), and is free to use.
"""

import logging
import os
from datetime import datetime
from typing import Set, Optional
import json

logger = logging.getLogger(__name__)


class StateManager:
    """
    Manages persistent state: posted URLs and execution logs.
    
    This implementation uses two strategies:
    1. In-memory set during execution (fast duplicate checks)
    2. File storage (persistent across runs)
    
    WHY hybrid approach: Loading all posted URLs into memory once at startup is
    fast for lookups. Saving to file at the end creates persistence.
    """
    
    def __init__(self, 
                 onedrive_client = None,
                 cache_size_limit: int = 10000):
        """
        Initialise state manager.
        
        Args:
            onedrive_client: Microsoft Graph API client (optional)
            cache_size_limit: Maximum URLs to keep in cache
        
        WHY cache_size_limit: If we keep URLs forever, the cache grows unbounded.
        10,000 URLs is ~250KB of data and covers ~6-9 months of feed content
        (assuming 40-50 new articles daily). Old URLs will be cycled out.
        """
        
        self.onedrive_client = onedrive_client
        self.cache_size_limit = cache_size_limit
        self.posted_urls: Set[str] = set()
        self.new_urls_this_run: Set[str] = set()
        
        # OneDrive paths (per spec)
        self.posted_urls_path = "/Projects/Monica/Feeds/feed2_posted_urls.txt"
        self.execution_log_path = "/Projects/Monica/Feeds/feed2_execution_log.txt"
        
        # Load existing posted URLs from storage
        self._load_posted_urls()
    
    def _load_posted_urls(self) -> None:
        """
        Load previously posted URLs from OneDrive.
        
        WHY try/except: OneDrive might be unavailable, or the file might not exist
        on first run. We fail gracefully and continue—missing some duplicates is
        better than crashing the function.
        """
        
        try:
            if self.onedrive_client:
                logger.info(f"Loading posted URLs from OneDrive: {self.posted_urls_path}")
                content = self.onedrive_client.get_file_contents(self.posted_urls_path)
                urls = [line.strip() for line in content.split('\n') if line.strip()]
                self.posted_urls = set(urls[-self.cache_size_limit:])  # Keep most recent N
                logger.info(f"Loaded {len(self.posted_urls)} posted URLs")
            else:
                logger.warning("OneDrive client not available. Using empty cache.")
                # In production, you'd want to fail more explicitly here
                self.posted_urls = set()
        
        except Exception as e:
            logger.warning(f"Error loading posted URLs: {e}. Starting with empty cache.")
            self.posted_urls = set()
    
    def is_duplicate(self, url: str) -> bool:
        """
        Check if article URL was already posted.
        
        Args:
            url: Article URL to check
        
        Returns:
            True if URL is in posted cache, False otherwise
        
        WHY simple lookup: Set membership check is O(1) average case, so it's
        very fast even with thousands of URLs.
        """
        
        return url in self.posted_urls
    
    def mark_posted(self, url: str) -> None:
        """
        Mark an article URL as posted.
        
        Args:
            url: Article URL that was just posted to Teams
        
        WHY track separately: We track new URLs posted this run so we can save
        them to disk at the end. This ensures we capture URLs even if disk write fails.
        """
        
        self.posted_urls.add(url)
        self.new_urls_this_run.add(url)
    
    def save_state(self) -> bool:
        """
        Save new posted URLs to OneDrive.
        
        WHY append mode: We append new URLs to the existing list rather than
        overwriting. This creates an audit trail—you can see the entire history
        of what was posted and when.
        
        Returns:
            True if save succeeded, False otherwise
        """
        
        if not self.new_urls_this_run:
            logger.info("No new URLs to save")
            return True
        
        try:
            if not self.onedrive_client:
                logger.warning("OneDrive client not available. URLs not saved.")
                return False
            
            logger.info(f"Saving {len(self.new_urls_this_run)} new URLs to OneDrive...")
            
            # Prepare content to append
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            content_lines = [f"{url}  # Posted on {timestamp}" for url in self.new_urls_this_run]
            content = '\n'.join(content_lines) + '\n'
            
            # Append to file
            self.onedrive_client.append_to_file(self.posted_urls_path, content)
            
            logger.info(f"Saved {len(self.new_urls_this_run)} new URLs")
            return True
        
        except Exception as e:
            logger.error(f"Error saving posted URLs: {e}")
            return False
    
    def log_execution(self, summary: str) -> bool:
        """
        Append execution summary to the execution log.
        
        Args:
            summary: Execution summary string (from main function)
        
        Returns:
            True if log succeeded, False otherwise
        
        WHY persistent log: Teams admins can review the log file to:
        - Verify the function ran on schedule
        - Check article counts from each source
        - Troubleshoot if cards stop appearing
        - Analyse trends (which sources produce most content)
        """
        
        try:
            if not self.onedrive_client:
                logger.warning("OneDrive client not available. Log not saved.")
                return False
            
            logger.info("Appending execution log to OneDrive...")
            
            # Append with separator for readability
            log_entry = summary + "\n" + ("=" * 80) + "\n"
            self.onedrive_client.append_to_file(self.execution_log_path, log_entry)
            
            logger.info("Execution logged successfully")
            return True
        
        except Exception as e:
            logger.error(f"Error logging execution: {e}")
            return False


class LocalStateManager(StateManager):
    """
    Alternative state manager using local file storage.
    
    WHY local alternative: During development/testing, OneDrive might not be
    available. This uses the Azure Function's /tmp directory for state.
    
    NOTE: Local state is NOT persistent across function restarts, which means
    duplicates will reappear. Use OneDrive StateManager in production.
    """
    
    def __init__(self):
        """Initialise with local file storage."""
        super().__init__(onedrive_client=None)
        self.local_data_dir = "/tmp/monica_feed2"
        self.posted_urls_file = f"{self.local_data_dir}/posted_urls.json"
        self.execution_log_file = f"{self.local_data_dir}/execution_log.txt"
        
        # Create directory if it doesn't exist
        os.makedirs(self.local_data_dir, exist_ok=True)
        self._load_posted_urls()
    
    def _load_posted_urls(self) -> None:
        """Load posted URLs from local JSON file."""
        try:
            if os.path.exists(self.posted_urls_file):
                with open(self.posted_urls_file, 'r') as f:
                    urls = json.load(f)
                self.posted_urls = set(urls[-self.cache_size_limit:])
                logger.info(f"Loaded {len(self.posted_urls)} posted URLs from local storage")
            else:
                logger.info("No local posted URLs file found. Starting fresh.")
                self.posted_urls = set()
        except Exception as e:
            logger.warning(f"Error loading local posted URLs: {e}")
            self.posted_urls = set()
    
    def save_state(self) -> bool:
        """Save new posted URLs to local JSON file."""
        if not self.new_urls_this_run:
            return True
        
        try:
            # Save all URLs as JSON for easy inspection
            with open(self.posted_urls_file, 'w') as f:
                json.dump(list(self.posted_urls), f, indent=2)
            logger.info(f"Saved {len(self.new_urls_this_run)} new URLs to local storage")
            return True
        except Exception as e:
            logger.error(f"Error saving local posted URLs: {e}")
            return False
    
    def log_execution(self, summary: str) -> bool:
        """Append execution log to local text file."""
        try:
            with open(self.execution_log_file, 'a') as f:
                f.write(summary + "\n")
                f.write("=" * 80 + "\n")
            logger.info("Execution logged to local storage")
            return True
        except Exception as e:
            logger.error(f"Error logging execution locally: {e}")
            return False
