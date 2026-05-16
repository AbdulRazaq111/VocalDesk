import requests
from bs4 import BeautifulSoup
from database import SessionLocal
import models
import re # Price extraction ke liye regular expressions

def extract_prices(text):
    # Ye function text mein se Rs. 500 ya PKR 1000 jaisi values nikalta hai
    prices = re.findall(r'(?:Rs\.?|PKR)\s?([\d,]+)', text)
    if prices:
        return f" EXACT PRICES FOUND: {', '.join(prices)}"
    return ""

def scrape_and_store(url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    try:
        print(f"Scraping shuru: {url}")
        # Timeout barha kar 30 kar diya hai taake heavy website load ho sakay
        response = requests.get(url, headers=headers, timeout=30)
        
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Website ka Title nikalna
            title = soup.title.string if soup.title else "Kababjees Menu Item"
            
            # Website ka main text nikalna (Cleaning unnecessary tags)
            for script_or_style in soup(["script", "style", "nav", "footer"]):
                script_or_style.decompose()
            
            clean_text = soup.get_text(separator=' ', strip=True)
            
            # --- NEW: Price Detection ---
            price_info = extract_prices(clean_text)
            final_data = f"{clean_text[:2000]} {price_info}"
            
            # Database mein save karna
            db = SessionLocal()
            new_entry = models.BusinessKnowledge(
                question=title[:100], 
                answer=final_data # Ab is mein text + exact price dono hain
            )
            db.add(new_entry)
            db.commit()
            db.close()
            print(f"Success! '{title}' ka data price ke sath save ho gaya.\n")
        else:
            print(f"Error: Status code {response.status_code}")
            
    except Exception as e:
        print(f"Masla aya: {e}")

# --- KABABJEES LINKS ---
if __name__ == "__main__":
    # In links ko aap browser mein check karke mazeed categories bhi add kar sakte hain
    links_to_test = [

    "https://kababjeesfriedchicken.com/category/Burgers-Deals-416638",
]
    
    for link in links_to_test:
        scrape_and_store(link)