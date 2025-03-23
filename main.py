from dotenv import load_dotenv
import os
import pandas as pd
from bs4 import BeautifulSoup
from fuzzywuzzy import fuzz
from geopy.distance import geodesic
from googleapiclient.discovery import build
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from webdriver_manager.chrome import ChromeDriverManager
import time
import openai
import re
import random
import datetime
import shutil
from fake_useragent import UserAgent
from concurrent.futures import ThreadPoolExecutor

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

# 全局缓存 Excel 数据
HOTEL_DATA = None

# 读取代理 IP 列表
def load_proxies(proxy_file):
    proxies = []
    with open(proxy_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line:
                parts = line.split(':')
                if len(parts) == 5:
                    ip, port, username, password, region = parts
                    proxies.append({
                        'ip': ip,
                        'port': port,
                        'username': username,
                        'password': password,
                        'region': region
                    })
    return proxies

# 随机选择代理 IP
def get_random_proxy(proxies):
    return random.choice(proxies)

# 读取已处理的酒店
def load_processed_hotels(processed_file):
    processed = set()
    if os.path.exists(processed_file):
        with open(processed_file, 'r') as f:
            for line in f:
                processed.add(line.strip())
    return processed

# 保存已处理的酒店
def save_processed_hotel(processed_file, hotel_code):
    with open(processed_file, 'a') as f:
        f.write(f"{hotel_code}\n")

# 读取 Excel 文件（缓存）
def load_hotel_data(file_path, num_rows=None, filter_prefix=None, processed_hotels=None):
    global HOTEL_DATA
    if HOTEL_DATA is None:
        HOTEL_DATA = pd.read_excel(file_path)
        print(f"Columns in Excel file: {HOTEL_DATA.columns.tolist()}")  # 打印列名以供调试
    
    df = HOTEL_DATA.copy()
    if processed_hotels:
        df = df[~df['code'].isin(processed_hotels)]  # 跳过已处理的酒店
    if filter_prefix:
        if 'code' not in df.columns:
            raise KeyError("Column 'code' not found in Excel file. Available columns: " + str(df.columns.tolist()))
        df = df[df['code'].str.startswith(filter_prefix, na=False)]
    if num_rows:
        if len(df) > num_rows:
            df = df.sample(n=num_rows, random_state=42)  # 随机挑选
        return df.to_dict('records')
    return df.to_dict('records')

# 使用 Google Custom Search API 搜索 Agoda 链接
def search_agoda_hotel(hotel_name):
    try:
        service = build("customsearch", "v1", developerKey=GOOGLE_API_KEY)
        query = f"site:agoda.com {hotel_name}"
        res = service.cse().list(q=query, cx=GOOGLE_CSE_ID, num=3).execute()
        links = [item['link'] for item in res.get('items', [])]
        print(f"Search results for '{hotel_name}': {links}")
        # 筛选出酒店详细页面 URL
        for link in links:
            if '/hotel/' in link and not any(x in link for x in ['/maps/', '/city/', '/reviews/']):
                return [link]
        return []  # 如果没有找到酒店详细页面，返回空列表
    except Exception as e:
        print(f"Error searching for {hotel_name}: {e}")
        return []

# 使用 Selenium 爬取 Agoda 页面信息
def scrape_agoda_page_selenium(url, driver):
    try:
        print(f"Scraping URL with Selenium: {url}")
        driver.get(url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        soup = BeautifulSoup(driver.page_source, 'html.parser')

        hotel_id = None
        twitter_image = soup.find("meta", {"name": "twitter:image"})
        if twitter_image and "hotelImages" in twitter_image["content"]:
            hotel_id = twitter_image["content"].split("hotelImages/")[1].split("/")[0]
        print(f"Hotel ID: {hotel_id}")

        hotel_url = None
        alternate_link = soup.find("link", {"rel": "alternate", "hreflang": "en"})
        if alternate_link:
            hotel_url = alternate_link["href"]
        print(f"Hotel URL: {hotel_url}")

        latitude = soup.find("meta", {"property": "place:location:latitude"})
        longitude = soup.find("meta", {"property": "place:location:longitude"})
        lat = float(latitude["content"]) if latitude else None
        lon = float(longitude["content"]) if longitude else None
        print(f"Latitude: {lat}, Longitude: {lon}")

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

        postal_code = soup.find("meta", {"property": "og:postal_code"})
        postal = postal_code["content"].strip() if postal_code else None
        print(f"Postal Code: {postal}")

        og_title = soup.find("meta", {"property": "og:title"})
        hotel_name = None
        if og_title:
            title = og_title["content"]
            # 清理 Agoda 酒店名称，去除广告词
            title = re.sub(r'^(Best Price on|Book|BOOK >>|Hotel Reviews of|Agoda\.com:.*|.* - Deals|.* in .+ \+ Reviews!|.* - Booking Deals \+ 2025 Promos|.* - โกเทมบะ - รีวิว แผนที่|.* \(Japan\))', '', title, flags=re.IGNORECASE).strip()
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

# 计算地理距离（以米为单位）
def calculate_distance(lat1, lon1, lat2, lon2):
    if lat1 and lon1 and lat2 and lon2:
        distance_km = geodesic((lat1, lon1), (lat2, lon2)).kilometers
        return distance_km * 1000  # 转换为米
    return 1000000  # 如果无法计算距离，返回一个默认值（1000公里 = 1000000米）

# 名称和地址相似度
def calculate_similarity(str1, str2, is_address=False):
    if str1 and str2:
        str1 = str1.lower().strip()
        str2 = str2.lower().strip()
        
        if is_address:
            str1 = re.sub(r'\broom\b\s*', '', str1, flags=re.IGNORECASE)
            str2 = re.sub(r'\broom\b\s*', '', str2, flags=re.IGNORECASE)
            str1 = re.sub(r',\s*(mapo-gu|jung-gu|songpa|gangseo|haeundae-gu|dongdaemun|hongdae|downtown jeju|jeju-si|busan|seoul|korea|south korea|jeju-do|ku|shi|cho|gun|si)', '', str1, flags=re.IGNORECASE)
            str2 = re.sub(r',\s*(mapo-gu|jung-gu|songpa|gangseo|haeundae-gu|dongdaemun|hongdae|downtown jeju|jeju-si|busan|seoul|korea|south korea|jeju-do|ku|shi|cho|gun|si)', '', str2, flags=re.IGNORECASE)
            str1 = str1.replace("-", " ").replace("  ", " ")
            str2 = str2.replace("-", " ").replace("  ", " ")
        else:
            str1 = re.sub(r'\bhotel\b\s*', '', str1, flags=re.IGNORECASE)
            str2 = re.sub(r'\bhotel\b\s*', '', str2, flags=re.IGNORECASE)
            # 去除常见的后缀
            suffixes = [
                r'in .+',
                r'natural hot spring',
                r'korea quality',
                r'\(korea quality\)'
            ]
            for suffix in suffixes:
                str1 = re.sub(r'\b' + suffix + r'\b', '', str1, flags=re.IGNORECASE)
                str2 = re.sub(r'\b' + suffix + r'\b', '', str2, flags=re.IGNORECASE)
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
                r'haeundae-gu',
                r'kusatsu-shi'
            ]
            for suffix in brand_suffixes:
                str1 = re.sub(r'\b' + suffix + r'\b', '', str1, flags=re.IGNORECASE)
                str2 = re.sub(r'\b' + suffix + r'\b', '', str2, flags=re.IGNORECASE)
            for city in cities:
                str1 = re.sub(r'\b' + city + r'\b', '', str1, flags=re.IGNORECASE)
                str2 = re.sub(r'\b' + city + r'\b', '', str2, flags=re.IGNORECASE)
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
        lines = response_text.split("\n")
        for line in lines:
            if line.startswith("Similarity:"):
                similarity = int(line.split(":")[1].strip())
                return similarity
        return 80  # 如果解析失败，返回默认值
    except Exception as e:
        print(f"Error in GPT fuzzy match: {e}")
        return 80  # 出错时返回默认值

# 处理单个酒店的函数（用于并行处理）
def process_hotel(hotel, driver, processed_file):
    start_time = time.time()
    print(f"\nProcessing hotel: {hotel['name_en']} (Code: {hotel.get('code', 'N/A')})")
    
    links = search_agoda_hotel(hotel['name_en'])
    if not links:
        print("No Agoda hotel page found.")
        return None, 0

    agoda_data = scrape_agoda_page_selenium(links[0], driver)
    if not agoda_data:
        print("Failed to scrape Agoda page.")
        return None, 0

    name_similarity = calculate_similarity(hotel['name_en'], agoda_data['hotel_name'])
    address_similarity = calculate_similarity(hotel['addr_en'].split(',')[0], agoda_data['address'].split(',')[0] if agoda_data['address'] else None, is_address=True)
    distance = calculate_distance(hotel['latitude'], hotel['longitude'], agoda_data['latitude'], agoda_data['longitude'])
    postal_match = True  # 如果 Agoda 页面缺少邮编，假设匹配
    if agoda_data['postal_code'] is not None:
        postal_match = str(hotel['postal_code']).strip() == str(agoda_data['postal_code']).strip()

    score = (0.3 * name_similarity + 0.2 * address_similarity + (0.3 if postal_match else 0)) / 0.8
    if distance < 1000:  # 1公里 = 1000米
        score += 40  # 提高距离加分
    elif distance > 5000:  # 5公里 = 5000米
        score += 10  # 1-5 公里不加不减，>5 公里加 10 分
    # 设置得分上限
    score = min(100, max(0, score))

    if 40 < score < 80:
        gpt_score = gpt_fuzzy_match(hotel, agoda_data)
        print(f"GPT Fuzzy Match Score: {gpt_score}")
        score = 0.4 * score + 0.6 * gpt_score
        score = min(100, max(0, score))  # 确保得分在 0-100 之间

    end_time = time.time()
    match_time = end_time - start_time

    # 包含导入文件的所有字段和抓取的所有字段
    result = {
        "local_hotel": hotel['name_en'],
        "code": hotel.get('code', 'N/A'),
        "country_code": hotel.get('country_code', ''),
        "area_en": hotel.get('area_en', ''),
        "latitude": hotel.get('latitude', ''),
        "longitude": hotel.get('longitude', ''),
        "addr_en": hotel.get('addr_en', ''),
        "postal_code": hotel.get('postal_code', ''),
        "agoda_hotel": agoda_data['hotel_name'],
        "name_similarity": name_similarity,
        "address_similarity": address_similarity,
        "distance_m": distance,  # 使用米为单位
        "postal_match": postal_match,
        "score": score,
        "match_time_seconds": match_time,
        "agoda_hotel_id": agoda_data['hotel_id'],
        "agoda_url": agoda_data['url'],
        "agoda_latitude": agoda_data['latitude'],
        "agoda_longitude": agoda_data['longitude'],
        "agoda_address": agoda_data['address'],
        "agoda_postal_code": agoda_data['postal_code'],
        "agoda_hotel_name": agoda_data['hotel_name']
    }

    # 保存已处理的酒店
    save_processed_hotel(processed_file, hotel['code'])

    print(f"Match Time: {match_time:.2f} seconds")
    return result, 1 if 40 < score < 80 else 0

# 主流程
def main(batch_size=500, total_hotels=9000, max_workers=4):
    file_path = "/Users/zhenggang/python/project/mapping-agoda/hotel_last3m.xlsx"
    processed_file = "processed_hotels.txt"
    proxy_file = "100.txt"

    # 加载代理 IP
    proxies = load_proxies(proxy_file)
    if not proxies:
        raise ValueError("No proxies found in 100.txt")

    # 加载已处理的酒店
    processed_hotels = load_processed_hotels(processed_file)

    # 计算需要处理的批次
    remaining_hotels = total_hotels - len(processed_hotels)
    if remaining_hotels <= 0:
        print("All hotels have been processed.")
        return

    num_batches = (remaining_hotels + batch_size - 1) // batch_size
    print(f"Total hotels to process: {remaining_hotels}")
    print(f"Number of batches: {num_batches} (batch size: {batch_size})")

    total_time = 0
    total_gcp_calls = 0
    total_gpt_calls = 0

    for batch in range(num_batches):
        print(f"\nProcessing batch {batch + 1}/{num_batches}")
        hotels = load_hotel_data(file_path, num_rows=batch_size, processed_hotels=processed_hotels)
        if not hotels:
            print("No more hotels to process in this batch.")
            continue

        results = []
        batch_time = 0
        batch_gcp_calls = 0
        batch_gpt_calls = 0

        # 创建一个 WebDriver 实例
        options = webdriver.ChromeOptions()
        options.add_argument("--headless")
        ua = UserAgent()
        user_agent = ua.random
        options.add_argument(f"user-agent={user_agent}")
        options.add_argument("--lang=en-US")
        options.add_experimental_option('prefs', {
            'intl.accept_languages': 'en-US,en'
        })
        driver = webdriver.Chrome(
            service=Service(ChromeDriverManager().install()),
            options=options
        )

        # 并行处理酒店
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_hotel, hotel, driver, processed_file) for hotel in hotels]
            for future in futures:
                result, gpt_call = future.result()
                if result:
                    results.append(result)
                batch_gcp_calls += 1
                batch_gpt_calls += gpt_call

        # 关闭 WebDriver
        driver.quit()

        if results:
            # 创建时间戳命名的文件夹
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            output_dir = f"results_{timestamp}"
            os.makedirs(output_dir, exist_ok=True)

            # 保存结果为 .xlsx 文件
            result_df = pd.DataFrame(results)
            output_file = os.path.join(output_dir, f"batch_{batch + 1}.xlsx")
            result_df.to_excel(output_file, index=False)
            print(f"Batch {batch + 1} results saved to {output_file}")

            total_time += batch_time
            total_gcp_calls += batch_gcp_calls
            total_gpt_calls += batch_gpt_calls

    # 计算总耗时和平均耗时
    avg_time_per_hotel = total_time / (total_hotels - len(processed_hotels)) if results else 0
    print(f"\nTotal Matching Time: {total_time:.2f} seconds")
    print(f"Average Time per Hotel: {avg_time_per_hotel:.2f} seconds")

    # 估算 9000 个酒店的耗时
    total_hotels = 9000
    estimated_total_time = avg_time_per_hotel * total_hotels
    print(f"Estimated Time for {total_hotels} Hotels: {estimated_total_time:.2f} seconds ({estimated_total_time / 3600:.2f} hours)")

    # 估算成本
    gcp_cost_per_1000 = 5  # 美元
    total_gcp_cost = (total_gcp_calls / 1000) * gcp_cost_per_1000 * (total_hotels / (total_hotels - len(processed_hotels)))
    print(f"Total GCP Calls: {total_gcp_calls} (for {total_hotels - len(processed_hotels)} hotels)")
    print(f"Estimated GCP Cost for {total_hotels} Hotels: ${total_gcp_cost:.2f}")

    tokens_per_call = 200 + 100  # 输入 + 输出
    cost_per_million_tokens_input = 0.50  # 美元
    cost_per_million_tokens_output = 1.50  # 美元
    total_tokens = tokens_per_call * total_gpt_calls * (total_hotels / (total_hotels - len(processed_hotels)))
    total_gpt_cost = (total_tokens / 1000000) * (cost_per_million_tokens_input + cost_per_million_tokens_output)
    print(f"Total GPT Calls: {total_gpt_calls} (for {total_hotels - len(processed_hotels)} hotels)")
    print(f"Estimated GPT Cost for {total_hotels} Hotels: ${total_gpt_cost:.2f}")

    total_cost = total_gcp_cost + total_gpt_cost
    print(f"Estimated Total Cost for {total_hotels} Hotels: ${total_cost:.2f}")

# 运行程序
if __name__ == "__main__":
    print("Running Version 2.1: Processing 9000 hotels in batches with optimization")
    main(batch_size=500, total_hotels=9000, max_workers=4)