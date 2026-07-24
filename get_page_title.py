import requests
from bs4 import BeautifulSoup

def get_page_title(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    return soup.title.string if soup.title else 'No title'

url = 'https://example.com'
title = get_page_title(url)
print(f"Title: {title}")