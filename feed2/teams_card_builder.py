"""
Teams Card Builder — Adaptive Card Generation

This module builds Teams Adaptive Cards from article metadata. Adaptive Cards
are JSON structures that Teams renders as rich, interactive messages.

WHY Adaptive Cards: They provide a consistent, modern card UI across Teams clients
(web, desktop, mobile). They support images, formatted text, and clickable actions.
They're more engaging than plain text and include full article metadata.
"""

import json
import logging
from typing import Dict

logger = logging.getLogger(__name__)


class TeamsCardBuilder:
    """
    Builds Teams Adaptive Cards from article metadata.
    """
    
    def build_card(self, article: Dict) -> Dict:
        """
        Build a Teams Adaptive Card from article metadata.
        
        Args:
            article: Dictionary with keys: headline, summary, link, image_url,
                    publish_date, source
        
        Returns:
            Adaptive Card message wrapper (ready to POST to Teams webhook)
        
        WHY structure: The returned dict matches the Teams webhook API format,
        which expects a "type" and "attachments" array. Each attachment is an
        Adaptive Card with specific schema version and structure.
        """
        
        # Validate required fields
        if not article.get('headline') or not article.get('link'):
            raise ValueError("Article missing headline or link")
        
        if not article.get('image_url'):
            raise ValueError("Article missing image_url")
        
        # Build the card body
        card_body = []
        
        # Add featured image
        # WHY at top: Images grab attention and preview the article topic
        card_body.append({
            "type": "Image",
            "url": article['image_url'],
            "size": "stretch",
            "spacing": "default"
        })
        
        # Add headline
        # WHY bolder + large: Draws reader attention to the main story
        card_body.append({
            "type": "TextBlock",
            "text": article['headline'],
            "weight": "bolder",
            "size": "large",
            "wrap": True
        })
        
        # Add summary (if present)
        # WHY subtle + small: Secondary text without overwhelming the headline
        if article.get('summary'):
            card_body.append({
                "type": "TextBlock",
                "text": article['summary'],
                "wrap": True,
                "spacing": "small",
                "isSubtle": True
            })
        
        # Add source and date metadata
        # WHY at bottom: Provides publication context without cluttering headline area
        metadata_text = f"{article['source']} • {article['publish_date']}"
        card_body.append({
            "type": "TextBlock",
            "text": metadata_text,
            "size": "small",
            "spacing": "small",
            "isSubtle": True
        })
        
        # Build the Adaptive Card
        adaptive_card = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.4",
            "body": card_body,
            "actions": [
                {
                    "type": "Action.OpenUrl",
                    "title": "Read Full Article",
                    "url": article['link']
                }
            ]
        }
        
        # Wrap in Teams message format
        # WHY this wrapper: Teams webhook API expects this exact structure.
        # The "attachment" tells Teams it's an Adaptive Card, not plain text.
        message = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": adaptive_card
                }
            ]
        }
        
        logger.debug(f"Built card for: {article['headline'][:60]}")
        
        return message
    
    def build_batch_cards(self, articles: list) -> list:
        """
        Build multiple cards from a list of articles.
        
        Args:
            articles: List of article dictionaries
        
        Returns:
            List of card messages (ready to POST to Teams webhook)
        
        WHY batch builder: If we ever want to post multiple articles in a single
        Teams message thread, this supports that workflow.
        """
        
        cards = []
        for article in articles:
            try:
                card = self.build_card(article)
                cards.append(card)
            except ValueError as e:
                logger.warning(f"Skipping invalid article: {e}")
        
        return cards
