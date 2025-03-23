from dotenv import load_dotenv
import os
import pandas as pd
from bs4 import BeautifulSoup
from fuzzywuzzy import fuzz
from geopy.distance import geodesic
from googleapiclient.discovery import build
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
import time
import openai
import re

# 加载 .env 文件
load_dotenv()

# 从环境变量中读取敏感数据
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GOOGLE_CSE_ID = os.getenv("GOOGLE_CSE_ID")
openai.api_key = os.getenv("OPENAI_API_KEY")

# 检查是否成功读取
if not GOOGLE_API_KEY or not GOOGLE_CSE_ID:
    raise ValueError("Missing GOOGLE_API_KEY or GOOGLE_CSE_ID in environment variables.")
if not openai.api_key:
    raise ValueError("Missing OPENAI_API_KEY in environment variables.")

# 读取 Excel 文件
def load_hotel_data(file_path, num_rows=10):
    df = pd.read_excel(file_path)
    return df.head(num_rows).to_dict('records')

# 使用 Google Custom Search API 搜索 Agoda 链接
def search_agoda_hotel(hotel_name):
    try:
        service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
        query = f"site:agoda.com {hotel_name}"
        res = service.cse().list(q=query, cx=GOOGLE_CSE_ID, num=3).execute()
        links = [item['link'] for item in res.get('items', [])]
        print(f"Search results for '{hotel_name}': {links}")
        return links
    except Exception as e:
        print(f"Error searching for {hotel_name}: {e}")
        return []

# 使用 Selenium 爬取 Agoda 页面信息
def scrape_agoda_page_selenium(url):
    try:
        print(f"Scraping URL with Selenium: {url}")
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36")
        options.add_argument("--lang=en-US")
        options.add_experimental_option('prefs', {
            'intl.accept_languages': 'en-US,en'
        })
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
        driver.get(url)
        time.sleep(3)
        soup = BeautifulSoup(driver.page_source, 'html.parser')
        driver.quit()

        # 提取酒店 ID
        hotel_id = None
        twitter_image = soup.find("meta", {"name": "twitter:image"})
        if twitter_image and "hotelImages" in twitter_image["content"]:
            hotel_id = twitter_image["content"].split("hotelImages/")[1].split("/")[0]
        print(f"Hotel ID: {hotel_id}")

        # 提取酒店 URL
        hotel_url = None
        alternate_link = soup.find("link", {"rel": "alternate", "hreflang": "en"})
        if alternate_link:
            hotel_url = alternate_link["href"]
        print(f"Hotel URL: {hotel_url}")

        # 提取经纬度
        latitude = soup.find("meta", {"property": "place:location:latitude"})
        longitude = soup.find("meta", {"property": "place:location:longitude"})
        lat = float(latitude["content"]) if latitude else None
        lon = float(longitude["content"]) if longitude else None
        print(f"Latitude: {lat}, Longitude: {lon}")

        # 提取地址（拼接街道、地区、国家）
        street_address = soup.find("meta", {"property": "og:street_address"})
        region = soup.find("meta", {"property": "og:region"})
        country = soup.find("meta", {"property": "og:country-name"})
        addr_parts = []
        if street_address:
            addr_parts.append(street_address["content"])
        if region:
            addr_parts.append(region["content"])
        if country:
            addr_parts.append(country["content"])
        addr = ", ".join(addr_parts) if addr_parts else None
        print(f"Address: {addr}")

        # 提取邮编
        postal_code = soup.find("meta", {"property": "og:postal_code"})
        postal = postal_code["content"].strip() if postal_code else None
        print(f"Postal Code: {postal}")

        # 提取酒店名称并清理
        og_title = soup.find("meta", {"property": "og:title"})
        hotel_name = None
        if og_title:
            title = og_title["content"]
            if ", " in title:
                hotel_name = title.split(", ")[0]
            elif " | " in title:
                hotel_name = title.split(" | ")[0]
            else:
                hotel_name = title
        print(f"Hotel Name: {hotel_name}")

        return {
            "hotel_id": hotel_id,
            "url": hotel_url,
            "latitude": lat,
            "longitude": lon,
            "address": addr,
            "postal_code": postal,
            "hotel_name": hotel_name
        }
    except Exception as e:
        print(f"Error scraping with Selenium {url}: {e}")
        return None

# 计算地理距离
def calculate_distance(lat1, lon1, lat2, lon2):
    if lat1 and lon1 and lat2 and lon2:
        return geodesic((lat1, lon1), (lat2, lon2)).kilometers
    return float('inf')

# 名称和地址相似度
def calculate_similarity(str1, str2, is_address=False):
    if str1 and str2:
        # 规范化：转换为小写，去除多余字符
        str1 = str1.lower().strip()
        str2 = str2.lower().strip()
        
        if is_address:
            # 地址规范化：去除 "Room" 前缀，统一连字符和空格
            str1 = re.sub(r'\broom\b\s*', '', str1, flags=re.IGNORECASE)
            str2 = re.sub(r'\broom\b\s*', '', str2, flags=re.IGNORECASE)
            str1 = str1.replace("-", " ").replace("  ", " ")
            str2 = str2.replace("-", " ").replace("  ", " ")
        else:
            # 名称规范化：去除品牌后缀和城市名
            brand_suffixes = [
                r'by best western signature collection',
                r'by best western',
                r'best western',
                r'signature collection'
            ]
            cities = [
                r'jeju',
                r'seoul',
                r'busan',
                r'downtown jeju',
                r'hongdae',
                r'myeong-dong',
                r'dongdaemun',
                r'gangseo',
                r'songpa',
                r'jung-gu',
                r'mapo-gu',
                r'haeundae-gu'
            ]
            for suffix in brand_suffixes:
                str1 = re.sub(r'\b' + suffix + r'\b', '', str1, flags=re.IGNORECASE)
                str2 = re.sub(r'\b' + suffix + r'\b', '', str2, flags=re.IGNORECASE)
            for city in cities:
                str1 = re.sub(r'\b' + city + r'\b', '', str1, flags=re.IGNORECASE)
                str2 = re.sub(r'\b' + city + r'\b', '', str2, flags=re.IGNORECASE)
            # 去除所有连字符和空格
            str1 = str1.replace("-", "").replace(" ", "")
            str2 = str2.replace("-", "").replace(" ", "")
        
        return fuzz.token_sort_ratio(str1, str2)
    return 0

# GPT 模糊匹配（使用 gpt-3.5-turbo 模型，适配 OpenAI 1.0.0+ API）
def gpt_fuzzy_match(local_data, agoda_data):
    try:
        prompt = f"""
Compare the following hotel data to determine if they refer to the same hotel:
Local Hotel: {local_data['name_en']}
Local Address: {local_data['addr_en']}
Agoda Hotel: {agoda_data['hotel_name']}
Agoda Address: {agoda_data['address']}
Return a similarity score between 0 and 100, where 100 means they are definitely the same hotel, and 0 means they are definitely different. Provide a brief explanation.
Format your response as:
Similarity: <score>
Explanation: <reason>
"""
        response = openai.chat.completions.create(
            model="gpt-3.5-turbo",
            messages=[
                {"role": "system", "content": "You are a helpful assistant that compares hotel data and provides similarity scores."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=100,
            temperature=0.5
        )
        response_text = response.choices[0].message.content.strip()
        # 解析 GPT 返回的相似度分数
        lines = response_text.split("\n")
        for line in lines:
            if line.startswith("Similarity:"):
                similarity = int(line.split(":")[1].strip())
                return similarity
        return 80  # 如果解析失败，返回默认值
    except Exception as e:
        print(f"Error in GPT fuzzy match: {e}")
        return 80  # 出错时返回默认值

# 主流程
def main():
    file_path = "/Users/zhenggang/python/project/mapping-agoda/hotel_last3m.xlsx"
    hotels = load_hotel_data(file_path, num_rows=10)
    print(f"Loaded {len(hotels)} hotels from Excel file.")

    results = []
    for hotel in hotels:
        print(f"\nProcessing hotel: {hotel['name_en']}")
        
        links = search_agoda_hotel(hotel['name_en'])
        if not links:
            print("No Agoda links found.")
            continue

        agoda_data = scrape_agoda_page_selenium(links[0])
        if not agoda_data:
            print("Failed to scrape Agoda page.")
            continue

        # 数据匹配
        name_similarity = calculate_similarity(hotel['name_en'], agoda_data['hotel_name'])
        address_similarity = calculate_similarity(hotel['addr_en'].split(',')[0], agoda_data['address'].split(',')[0] if agoda_data['address'] else None, is_address=True)
        distance = calculate_distance(hotel['latitude'], hotel['longitude'], agoda_data['latitude'], agoda_data['longitude'])
        postal_match = str(hotel['postal_code']).strip() == str(agoda_data['postal_code']).strip()

        # 综合评分（调整权重）
        score = (0.3 * name_similarity + 0.2 * address_similarity + (0.3 if postal_match else 0)) / 0.8
        if distance < 1:
            score += 20
        elif distance > 5:
            score -= 20

        if 40 < score < 80:
            gpt_score = gpt_fuzzy_match(hotel, agoda_data)
            print(f"GPT Fuzzy Match Score: {gpt_score}")
            score = 0.4 * score + 0.6 * gpt_score  # 调整 GPT 分数的权重

        result = {
            "local_hotel": hotel['name_en'],
            "agoda_hotel": agoda_data['hotel_name'],
            "name_similarity": name_similarity,
            "address_similarity": address_similarity,
            "distance_km": distance,
            "postal_match": postal_match,
            "score": score,
            "agoda_data": agoda_data
        }
        results.append(result)

        time.sleep(1)

    print("\nMatching Results:")
    for result in results:
        print(f"\nLocal Hotel: {result['local_hotel']}")
        print(f"Agoda Hotel: {result['agoda_hotel']}")
        print(f"Name Similarity: {result['name_similarity']}")
        print(f"Address Similarity: {result['address_similarity']}")
        print(f"Distance (km): {result['distance_km']}")
        print(f"Postal Code Match: {result['postal_match']}")
        print(f"Final Score: {result['score']}")
        print(f"Agoda Data: {result['agoda_data']}")
        print("---")

    result_df = pd.DataFrame(results)
    result_df.to_csv("matching_results.csv", index=False)
    print("\nResults saved to matching_results.csv")

if __name__ == "__main__":
    main()