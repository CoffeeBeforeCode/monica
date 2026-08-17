"""
OneDrive Client — Microsoft Graph API Integration

This module provides a client for reading/writing files to OneDrive using
Microsoft Graph API. It handles authentication, file operations, and error handling.

WHY separate module: Keeps OneDrive integration decoupled from state management.
This allows StateManager to work with different storage backends (local, OneDrive, etc.)

WHY Microsoft Graph: It's the official Microsoft API for OneDrive access. It handles
authentication, permissions, and throttling automatically.
"""

import logging
from typing import Optional
from azure.identity import ClientSecretCredential, DefaultAzureCredential
import requests
import os

logger = logging.getLogger(__name__)


class OneDriveClient:
    """
    Handles file operations on OneDrive via Microsoft Graph API.
    
    Supports both delegated (user) and application credentials.
    """
    
    GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"
    
    def __init__(self, credential=None, use_default: bool = True):
        """
        Initialise OneDrive client.
        
        Args:
            credential: Azure credential object (ClientSecretCredential, etc.)
            use_default: If True, use DefaultAzureCredential (app identity)
        
        WHY DefaultAzureCredential: It tries multiple auth methods in order:
        1. Environment variables (AZURE_CLIENT_ID, etc.)
        2. Managed identity (if running on Azure)
        3. Azure CLI credentials
        4. Visual Studio credentials
        
        This makes local testing and Azure deployment work without code changes.
        """
        
        if credential:
            self.credential = credential
        elif use_default:
            self.credential = DefaultAzureCredential()
        else:
            raise ValueError("Either provide credential or set use_default=True")
        
        self.session = requests.Session()
        self._ensure_auth_header()
    
    def _ensure_auth_header(self) -> None:
        """
        Get auth token and set it in session headers.
        
        WHY before requests: Microsoft Graph requires Authorization header.
        We get the token once and reuse it for multiple requests.
        """
        
        try:
            token = self.credential.get_token("https://graph.microsoft.com/.default").token
            self.session.headers.update({
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            })
            logger.debug("Auth header set successfully")
        except Exception as e:
            logger.error(f"Error getting auth token: {e}")
            raise
    
    def get_file_contents(self, file_path: str) -> str:
        """
        Read file contents from OneDrive.
        
        Args:
            file_path: OneDrive path (e.g., "/Projects/Monica/Feeds/posted_urls.txt")
        
        Returns:
            File contents as string
        
        WHY abstraction: Hides Microsoft Graph API details. Callers just provide
        a path and get back file contents.
        
        Raises:
            FileNotFoundError if file doesn't exist
            Exception if API call fails
        """
        
        try:
            # Construct Graph API URL
            # WHY itemWithPath: It's the simplest way to reference files by path
            encoded_path = file_path.replace(' ', '%20')
            url = f"{self.GRAPH_ENDPOINT}/me/drive/root:{encoded_path}:/content"
            
            logger.debug(f"Fetching file from OneDrive: {file_path}")
            
            response = self.session.get(url)
            response.raise_for_status()
            
            logger.info(f"Successfully read {file_path} from OneDrive")
            return response.text
        
        except requests.exceptions.HTTPError as e:
            if response.status_code == 404:
                logger.warning(f"File not found: {file_path}")
                raise FileNotFoundError(f"OneDrive file not found: {file_path}")
            else:
                logger.error(f"HTTP error reading file: {e}")
                raise
    
    def create_file(self, file_path: str, content: str) -> bool:
        """
        Create or overwrite a file on OneDrive.
        
        Args:
            file_path: OneDrive path
            content: File contents
        
        Returns:
            True if successful, False otherwise
        
        WHY separate from write: create_file overwrites; write/append adds content.
        This gives callers precise control over behavior.
        """
        
        try:
            encoded_path = file_path.replace(' ', '%20')
            url = f"{self.GRAPH_ENDPOINT}/me/drive/root:{encoded_path}:/content"
            
            logger.debug(f"Creating file on OneDrive: {file_path}")
            
            response = self.session.put(url, data=content.encode('utf-8'))
            response.raise_for_status()
            
            logger.info(f"Successfully created {file_path} on OneDrive")
            return True
        
        except Exception as e:
            logger.error(f"Error creating file on OneDrive: {e}")
            return False
    
    def append_to_file(self, file_path: str, content: str) -> bool:
        """
        Append content to an existing file on OneDrive.
        
        Args:
            file_path: OneDrive path
            content: Content to append
        
        Returns:
            True if successful, False otherwise
        
        WHY append operation: For logs and posted URL tracking, we want to append
        rather than overwrite. This preserves history.
        
        Implementation: Read existing file, append new content, write back.
        This is atomic-ish (if API fails, no data lost).
        """
        
        try:
            # Read existing content
            try:
                existing = self.get_file_contents(file_path)
            except FileNotFoundError:
                logger.info(f"File doesn't exist, creating new: {file_path}")
                existing = ""
            
            # Append new content
            new_content = existing + content
            
            # Write back
            return self.create_file(file_path, new_content)
        
        except Exception as e:
            logger.error(f"Error appending to file on OneDrive: {e}")
            return False
    
    def delete_file(self, file_path: str) -> bool:
        """
        Delete a file from OneDrive.
        
        Args:
            file_path: OneDrive path
        
        Returns:
            True if successful, False otherwise
        """
        
        try:
            encoded_path = file_path.replace(' ', '%20')
            url = f"{self.GRAPH_ENDPOINT}/me/drive/root:{encoded_path}"
            
            response = self.session.delete(url)
            response.raise_for_status()
            
            logger.info(f"Successfully deleted {file_path} from OneDrive")
            return True
        
        except Exception as e:
            logger.error(f"Error deleting file from OneDrive: {e}")
            return False
    
    def ensure_folder_exists(self, folder_path: str) -> bool:
        """
        Ensure a folder exists on OneDrive (create if missing).
        
        Args:
            folder_path: OneDrive path (e.g., "/Projects/Monica/Feeds")
        
        Returns:
            True if folder exists or was created, False on error
        
        WHY helper: Before writing files, ensure the parent folder exists.
        Microsoft Graph requires this.
        """
        
        try:
            # Try to get folder metadata (will fail if doesn't exist)
            encoded_path = folder_path.replace(' ', '%20')
            url = f"{self.GRAPH_ENDPOINT}/me/drive/root:{encoded_path}"
            
            response = self.session.get(url)
            
            if response.status_code == 200:
                logger.debug(f"Folder exists: {folder_path}")
                return True
            
            # Folder doesn't exist; try to create it
            # This requires creating parent folders recursively
            return self._create_folder_recursive(folder_path)
        
        except Exception as e:
            logger.error(f"Error checking/creating folder: {e}")
            return False
    
    def _create_folder_recursive(self, folder_path: str) -> bool:
        """
        Create folder and parent folders recursively.
        
        WHY internal method: Complex logic; kept separate from public API.
        """
        
        try:
            # Split path into parts
            parts = [p for p in folder_path.split('/') if p]
            
            # Create each folder level
            current_path = ""
            for part in parts:
                current_path += f"/{part}"
                
                # Try to create this folder
                url = f"{self.GRAPH_ENDPOINT}/me/drive/root/children"
                payload = {
                    "name": part,
                    "folder": {}
                }
                
                response = self.session.post(url, json=payload)
                
                # 409 = folder already exists, which is fine
                if response.status_code not in [201, 409]:
                    logger.warning(f"Unexpected response creating folder: {response.status_code}")
            
            logger.info(f"Folder hierarchy created: {folder_path}")
            return True
        
        except Exception as e:
            logger.error(f"Error creating folder hierarchy: {e}")
            return False
