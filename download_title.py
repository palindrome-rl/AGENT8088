import requests
from bs4 import BeautifulSoup

def download_title(url):
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    title = soup.title.string if soup.title else 'No title found'
    return title

title = download_title('https://www.example.com')
print(f"Title: {title}")
with open('title.txt', 'w') as f:
    f.write(title)
print(f"Title saved to title.txt")