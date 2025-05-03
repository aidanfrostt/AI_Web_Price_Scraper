#AI_Web_Scraping Tool
import requests
import csv
import os
import time
import re
import datetime
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import ollama

CSV_FILE = "products.csv"

def display_products():
    """Display existing products from CSV in a nice table format with prices"""
    if not os.path.exists(CSV_FILE):
        return
    
    with open(CSV_FILE, mode="r", newline="") as file:
        reader = csv.DictReader(file)
        products = list(reader)
        
    if not products:
        return
        
    print("\n📋 Current Wine Products:")
    print("-" * 90)
    print(f"{'Name':<30} | {'Description':<20} | {'Source':<10} | {'Price':<10} | {'Last Updated'}")
    print("-" * 90)
    for product in products:
        # Format price with $ and 2 decimal places if available
        price = product.get('price', 'N/A')
        if price != 'N/A':
            try:
                price = f"${float(price):.2f}"
            except ValueError:
                pass
        
        # Format last updated date (show only date if available)
        last_updated = product.get('last_updated', '')
        if last_updated:
            last_updated = last_updated.split()[0]  # Just show date without time
        
        print(f"{product['name'][:28]:<30} | {product['description'][:18]:<20} | {product['source']:<10} | {price:<10} | {last_updated}")
    print("-" * 90)

def validate_url(url):
    """Check if URL is valid by attempting to load it with Selenium"""
    try:
        options = webdriver.ChromeOptions()
        options.add_argument('--headless')  # Run in headless mode
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        driver = webdriver.Chrome(options=options)

        # Attempt to load the URL
        driver.get(url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        driver.quit()
        return True
    except Exception as e:
        print(f"Selenium validation failed: {e}")
        return False

def extract_price_from_any_website(url, html_content, product_name):
    """
    Universal price extraction using multi-layered approach:
    1. Try direct CSS selectors (fast)
    2. Try semantic HTML parsing (medium speed)
    3. Use Selenium for specific price element extraction (fallback)
    4. Fall back to AI analysis (slower but most comprehensive)
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    
    # 1. First try common CSS selectors (fastest method)
    common_selectors = [
        'product-price', '[itemprop="price"]',
        '[itemprop="priceCurrency"]',
        '.price', '.product-price', '.price-current',
        '.current-price', '.final-price', '.sales-price',
        '.price--large', '.productPrice', '.amount',
        '.value', '.pricing', '.product__price',
        '.price-container', '.price-wrapper', 'value'
    ]
    
    for selector in common_selectors:
        element = soup.select_one(selector)
        if element:
            price = extract_numeric_price(element.get_text())
            if price:
                return price

    # 2. Try semantic HTML parsing (medium speed)
    semantic_patterns = [
        # Price in meta tags
        {'tag': 'meta', 'attrs': {'itemprop': 'price'}, 'attr': 'content'},
        {'tag': 'meta', 'attrs': {'property': 'product:price:amount'}, 'attr': 'content'},
        # Price in JSON-LD
        {'tag': 'script', 'attrs': {'type': 'application/ld+json'}, 'json_path': ['offers.price']},
        # Common price text patterns
        {'text_pattern': r'price[\s:]*[\$€£]?\s*(\d+[\.,]?\d*)'},
        {'text_pattern': r'[\$€£]\s*(\d+[\.,]?\d*)'}
    ]
    
    price = try_semantic_patterns(soup, semantic_patterns)
    if price:
        return price

    # 3. Use Selenium for specific price element extraction (fallback)
    try:
        options = webdriver.ChromeOptions()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        driver = webdriver.Chrome(options=options)
        
        driver.get(url)

        # Try to find the sale price
        try:
            sale_price_element = WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//div[@data-bind=\"text: '$' + _source.currentPrice\"]"))
            )
            sale_price = sale_price_element.text
            price = extract_numeric_price(sale_price)
            if price:
                driver.quit()
                return price
        except Exception:
            sale_price = None  # Handle the exception by setting sale_price to None

        # Try to find the regular price as a fallback
        try:
            regular_price_element = driver.find_element(By.XPATH, "//div[@data-bind=\"price: _source.regularPrice\"]")
            regular_price = regular_price_element.text
            price = extract_numeric_price(regular_price)
            if price:
                driver.quit()
                return price
        except Exception:
            regular_price = None  # Handle the exception by setting regular_price to None

        driver.quit()
    except Exception as e:
        print(f"Selenium price extraction failed: {e}")

    # 4. Fall back to AI analysis (most comprehensive but slower)
    return extract_price_with_ai_fallback(soup, url, product_name)

def try_semantic_patterns(soup, patterns):
    """Try various semantic patterns to extract price"""
    for pattern in patterns:
        if 'tag' in pattern:
            elements = soup.find_all(pattern['tag'], pattern.get('attrs', {}))
            for element in elements:
                if 'attr' in pattern:
                    price = extract_numeric_price(element.get(pattern['attr']))
                    if price:
                        return price
                elif 'json_path' in pattern:
                    try:
                        data = json.loads(element.string)
                        for key in pattern['json_path']:
                            data = data.get(key, {})
                        if isinstance(data, (int, float)):
                            return float(data)
                        elif isinstance(data, str):
                            return extract_numeric_price(data)
                    except:
                        continue
        elif 'text_pattern' in pattern:
            matches = re.search(pattern['text_pattern'], soup.get_text(), re.IGNORECASE)
            if matches:
                return extract_numeric_price(matches.group(1))
    return None

def extract_price_with_ai_fallback(soup, url, product_name=None):
    """AI-powered fallback with improved product context handling"""
    try:
        # Clean HTML to reduce noise
        for element in soup(['script', 'style', 'nav', 'footer', 'header', 'iframe', 'img']):
            element.decompose()
        
        # Get product name keywords for context
        product_keywords = set()
        if product_name:
            product_keywords = set(word.lower() for word in re.findall(r'\w+', product_name) if len(word) > 3)
        
        # Find all numeric elements with context
        numeric_elements = []
        for element in soup.find_all(['span', 'div', 'p', 'td', 'li', 'h1', 'h2', 'h3', 'h4']):
            text = element.get_text(separator=' ', strip=True)
            if not text or len(text) > 100:  # Skip very long texts
                continue
                
            # Score based on price and product context
            score = 0
            element_html = str(element)
            element_text = text.lower()
            
            # Price indicators
            if re.search(r'[\$€£]\s*\d+', text):
                score += 10
            if 'price' in element.get('class', []):
                score += 8
            if any(word in element_text for word in ['only', 'now', 'special']):
                score += 5
                
            # Product context indicators
            if product_keywords:
                keyword_matches = sum(1 for kw in product_keywords if kw in element_text)
                score += keyword_matches * 3
                
            # Negative indicators
            if any(word in element_text for word in ['total', 'subtotal', 'tax', 'shipping']):
                score -= 8
            if 'original' in element_text or 'was' in element_text:
                score -= 5
                
            if score > 0:
                numeric_elements.append({
                    'text': text,
                    'html': element_html,
                    'score': score
                })
        
        # Sort by confidence score
        numeric_elements.sort(key=lambda x: x['score'], reverse=True)
        
        # Prepare AI context
        context = "Potential price elements from webpage:\n"
        for i, element in enumerate(numeric_elements[:5], 1):
            context += f"\nCandidate {i} (score: {element['score']}):\n"
            context += f"Text: {element['text']}\n"
            context += f"HTML: {element['html']}\n"
        
        # Create optimized prompt with product context
        prompt = f"""Analyze these webpage elements and extract JUST the current price for the product: {product_name or 'unknown product'}.
        
        IMPORTANT RULES:
        1. Match the price that most closely relates to the product name
        2. Prioritize prices near product names or images
        3. Ignore crossed-out prices (e.g., "~~$50~~") or comparison prices
        4. Reject prices that appear in unrelated sections (cart totals, shipping fees)
        5. If multiple valid prices exist, choose the one with strongest product association
        6. Return ONLY the numeric value (e.g., 19.99) or 'Not found' if uncertain
        
        ANALYSIS CONTEXT:
        {context}
        
        FINAL PRICE DECISION:"""
        
        # Get AI response
        response = ollama.chat(
            model="deepseek-r1:1.5b",
            messages=[{"role": "user", "content": prompt}],
            options={
                'temperature': 0.1,  # Slightly creative to handle edge cases
                'num_ctx': 4096  # Larger context window
            }
        )
        
        # Process response
        response_text = response['message']['content'].strip()
        print(f"AI response: {response_text}")
        
        return extract_numeric_price(response['message']['content'])
        # Extract price from response

        
    except Exception as e:
        print(f"AI extraction error: {e}")
        return None

def extract_numeric_price(text):
    """Extract first valid price from text"""
    if not text:
        return None
        
    # Find all price-like patterns
    matches = re.finditer(r"""
        (?:^|\s)          # Start or whitespace
        (?:[\$€£]?\s*)?   # Optional currency symbol
        (\d{1,3}          # 1-3 digits
        (?:[\.,]\d{2,3})? # Optional decimal with 2-3 digits
        (?:[\.,]\d{3})*)  # Optional thousands separators
        (?:\s|$)          # End or whitespace
    """, text, re.VERBOSE)
    
    # Convert matches to float values
    prices = []
    for match in matches:
        try:
            price_str = match.group(1).replace(',', '')
            prices.append(float(price_str))
        except ValueError:
            continue
    
    # Return the most likely price (middle value if multiple)
    if prices:
        prices.sort()
        return prices[len(prices)//2]
    
    return None

def get_price_with_ai(url, html_content, product_name):
    """Universal price extraction interface"""
    try:
        # First try fast methods
        print(product_name)
        price = extract_price_from_any_website(url, html_content, product_name)
        
        if price is None:
            print("Using AI fallback for price detection")
            soup = BeautifulSoup(html_content, 'html.parser')

            price = extract_price_with_ai_fallback(soup, url, product_name)
        
        return price
        
    except Exception as e:
        print(f"Price extraction error: {e}")
        return None

def add_product():
    """Add a new product to the CSV file"""
    print("\nAdd New Product")
    name = input("Product name: ").strip()
    description = input("Description: ").strip()
    source = input("Source (website name): ").strip()
    url = input("URL: ").strip()
    
    # Try to fetch price
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            price = get_price_with_ai(url, response.text,name)
        else:
            print("Could not fetch webpage with requests. Trying Selenium...")
            price = fetch_price_with_selenium(url, name)
        
        if price is None:
            print("Could not determine price automatically. Please enter manually.")
            while True:
                try:
                    price = float(input("Enter price: "))
                    break
                except ValueError:
                    print("Invalid price. Please enter a numeric value.")
    except Exception as e:
        print(f"Error fetching price: {e}")
        print("Trying Selenium as a fallback...")
        price = fetch_price_with_selenium(url,name)
        if price is None:
            while True:
                try:
                    price = float(input("Enter price: "))
                    break
                except ValueError:
                    print("Invalid price. Please enter a numeric value.")
    
    # Add to CSV
    fieldnames = ['name', 'description', 'source', 'url', 'price', 'last_updated']
    new_product = {
        'name': name,
        'description': description,
        'source': source,
        'url': url,
        'price': price,
        'last_updated': datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    
    file_exists = os.path.exists(CSV_FILE)
    with open(CSV_FILE, mode='a', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        if not file_exists:
            writer.writeheader()
        writer.writerow(new_product)
    
    print(f"Product '{name}' added successfully!")

def fetch_price_with_selenium(url, product_name):
    """Fetch price using Selenium as a fallback"""
    try:
        options = webdriver.ChromeOptions()
        options.add_argument('--headless')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        driver = webdriver.Chrome(options=options)
        
        driver.get(url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        html_content = driver.page_source
        driver.quit()
        
        # Extract price using the existing AI-based method
        print(product_name)
        return get_price_with_ai(url, html_content,product_name)
    except Exception as e:
        print(f"Selenium error: {e}")
        return None

def edit_product():
    """Edit or delete an existing product in the CSV file"""
    if not os.path.exists(CSV_FILE):
        print("No products found. Please add products first.")
        return
        
    # Read current products
    with open(CSV_FILE, mode="r", newline="") as file:
        reader = csv.DictReader(file)
        products = list(reader)
        fieldnames = reader.fieldnames
        
    # Display products with numbers
    print("\nSelect a product to edit:")
    for i, product in enumerate(products, 1):
        print(f"{i}. {product['name']} ({product['source']}) - ${product.get('price', 'N/A')}")
    
    while True:
        try:
            choice = int(input("Enter product number (0 to cancel): "))
            if 0 <= choice <= len(products):
                break
            print("Invalid choice. Please try again.")
        except ValueError:
            print("Please enter a number.")
    
    if choice == 0:
        return
        
    selected_product = products[choice-1]
    
    # Edit or delete menu
    print(f"\nSelected: {selected_product['name']} ({selected_product['source']})")
    print("1. Edit product")
    print("2. Delete product")
    print("0. Cancel")
    
    while True:
        action = input("Choose action: ").strip()
        if action in ['0', '1', '2']:
            break
        print("Invalid choice. Please enter 0, 1 or 2")
    
    if action == '0':
        return
    elif action == '2':
        # Delete product
        confirm = input(f"Are you sure you want to delete {selected_product['name']}? (y/n): ").lower()
        if confirm == 'y':
            del products[choice-1]
            # Rewrite CSV without the deleted product
            with open(CSV_FILE, mode='w', newline='') as file:
                writer = csv.DictWriter(file, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(products)
            print("Product deleted successfully!")
        return
    
    # Edit product flow
    print("\nEditable fields:")
    fields = [f for f in fieldnames if f not in ['last_updated']]  # Don't allow direct last_updated editing
    for i, field in enumerate(fields, 1):
        print(f"{i}. {field}: {selected_product.get(field, 'N/A')}")
    
    while True:
        try:
            field_choice = int(input("Select field to edit (0 to cancel): "))
            if 0 <= field_choice <= len(fields):
                break
            print("Invalid choice. Please try again.")
        except ValueError:
            print("Please enter a number.")
    
    if field_choice == 0:
        return
        
    field_to_edit = fields[field_choice-1]
    new_value = input(f"Enter new {field_to_edit} (current: {selected_product.get(field_to_edit, 'N/A')}): ").strip()
    
    # Validate URL if editing URL field
    if field_to_edit == 'url' and not validate_url(new_value):
        print("Invalid URL or unable to access the website")
        return
        
    # Validate price if editing price field
    if field_to_edit == 'price':
        try:
            new_value = float(new_value)
        except ValueError:
            print("Invalid price. Must be a number.")
            return
    
    # Update the product
    selected_product[field_to_edit] = new_value
    selected_product['last_updated'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Rewrite the entire CSV
    with open(CSV_FILE, mode='w', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(products)
    
    print("Product updated successfully!")

def get_prices():
    """Fetch current prices for products"""
    if not os.path.exists(CSV_FILE):
        print("No products found. Please add products first.")
        return
        
    with open(CSV_FILE, mode="r", newline="") as file:
        reader = csv.DictReader(file)
        products = list(reader)
    
    print("\nSelect products to update:")
    print("1. Update all products")
    print("2. Select specific products")
    print("0. Cancel")
    
    while True:
        choice = input("Enter choice: ")
        if choice in ['0', '1', '2']:
            break
        print("Invalid choice. Please try again.")
    
    if choice == '0':
        return
    elif choice == '1':
        selected_products = products
    else:
        # Display products with numbers
        print("\nSelect products to update (comma-separated numbers):")
        for i, product in enumerate(products, 1):
            print(f"{i}. {product['name']} ({product['source']})")
        
        while True:
            selections = input("Enter product numbers (e.g., 1,3,5): ").strip()
            try:
                selected_indices = [int(num.strip()) for num in selections.split(',')]
                if all(1 <= i <= len(products) for i in selected_indices):
                    selected_products = [products[i-1] for i in selected_indices]
                    break
                print("Some numbers are out of range. Please try again.")
            except ValueError:
                print("Invalid input. Please enter numbers separated by commas.")
    
    # Update prices
    print("\n🔄 Updating prices...")
    updated_count = 0
    
    for product in selected_products:
        print(f"\nFetching price for {product['name']} ({product['source']})...")
        try:
            response = requests.get(product['url'], timeout=10)
            if response.status_code == 200:
                price = get_price_with_ai(product['url'], response.text, product['name'])
            else:
                print(f"Failed to fetch webpage with requests (HTTP {response.status_code}). Trying Selenium...")
                price = fetch_price_with_selenium(product['url'], product['name'])
            
            if price is not None:
                product['price'] = price
                product['last_updated'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"New price: ${price:.2f}")
                updated_count += 1
            else:
                print("Could not determine new price. Keeping existing price.")
        except Exception as e:
            print(f"Error fetching price: {e}. Trying Selenium as a fallback...")
            price = fetch_price_with_selenium(product['url'], product['name'])
            if price is not None:
                product['price'] = price
                product['last_updated'] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"✅ New price: ${price:.2f}")
                updated_count += 1
            else:
                print("Could not determine new price. Keeping existing price.")
    
    # Save updates
    if updated_count > 0:
        fieldnames = ['name', 'description', 'source', 'url', 'price', 'last_updated']
        with open(CSV_FILE, mode='w', newline='') as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(products)
        print(f"\nUpdated {updated_count} prices successfully!")
    else:
        print("\n No prices were updated.")

def main_menu():
    """Display main menu and handle user choices"""
    while True:
        display_products()
        
        print("\nMain Menu:")
        print("1. Add Product")
        print("2. Edit Product")
        print("3. Get Prices Now")
        print("0. Exit")
        
        choice = input("Enter choice: ").strip()
        
        if choice == '1':
            add_product()
        elif choice == '2':
            edit_product()
        elif choice == '3':
            get_prices()
        elif choice == '0':
            print("Goodbye!")
            break
        else:
            print("Invalid choice. Please try again.")
        
        input("\nPress Enter to continue...")

if __name__ == "__main__":
    print("Price Scraping AI Tool")
    print("========================")
    main_menu()