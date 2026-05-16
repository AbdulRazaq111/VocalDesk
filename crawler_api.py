import requests
from database import SessionLocal
import models

def sync_full_menu():
    # Final API URL
    api_url = "https://console.indolj.io/mobileapp/WebApi/StructuredMenu?domain=kababjeesfriedchicken.com&json=1&api_version=0.14.73"
    
    # "Real User" Headers: Ye headers server ko dhoka denge ke request Chrome browser se aa rahi hai
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'authorization': 'Bearer 614465299|Lpv1jatuD3gYpY5YP4pqPKTExnd0rrJfGswiIY5D',
        'origin': 'https://kababjeesfriedchicken.com',
        'referer': 'https://kababjeesfriedchicken.com/',
        'Connection': 'keep-alive',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache'
    }

    try:
        print("Kababjees API se Fresh Data uthaya ja raha hai (Real User Mode)...")
        
        # Request with updated headers and timeout
        response = requests.get(api_url, headers=headers, timeout=20)
        
        if response.status_code != 200:
            print(f"Server ne error diya: {response.status_code}")
            return

        data = response.json()
        db = SessionLocal()
        
        # Database clear karein taake hamesha fresh data rahe
        db.query(models.BusinessKnowledge).delete()
        print("Database clear kar diya gaya. Naya data save ho raha hai...")

        # Kababjees structure check
        menu_details = data.get('details', {})
        
        count = 0
        for cat_id, category in menu_details.items():
            cat_name = category.get('title', 'Menu Item')
            products = category.get('products', [])
            
            for item in products:
                name = item.get('product_name')
                desc = item.get('product_description', '')
                
                # Variants (Deals aur different sizes ke liye)
                variants = item.get('variants', [])
                if variants:
                    for v in variants:
                        # Price ko verify karein (discounted ya actual)
                        price = v.get('price')
                        v_name = f"{name} ({v.get('variant_name')})"
                        
                        new_entry = models.BusinessKnowledge(
                            question=v_name,
                            answer=f"The price of {v_name} is Rs. {price}. Details: {desc}"
                        )
                        db.add(new_entry)
                        count += 1
                        print(f"Saved: {v_name} - Rs. {price}")
                else:
                    # Direct product entry
                    price = item.get('price')
                    if price and price != 0:
                        new_entry = models.BusinessKnowledge(
                            question=name,
                            answer=f"The price of {name} is Rs. {price}. Details: {desc}"
                        )
                        db.add(new_entry)
                        count += 1
                        print(f"Saved: {name} - Rs. {price}")

        # Data save (Commit)
        db.commit()
        db.close()
        print(f"\n--- SUCCESS! ---")
        print(f"Total {count} items database mein updated prices ke saath save ho gaye hain.")
        
    except Exception as e:
        print(f"Sync mein masla aya: {e}")

if __name__ == "__main__":
    sync_full_menu()