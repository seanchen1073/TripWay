import os
from decimal import Decimal, InvalidOperation
import time
import requests
from django.core.management.base import BaseCommand
from spots.models import Spot
from dotenv import load_dotenv

load_dotenv()

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchNearby"
PHOTO_MEDIA_URL_TMPL = "https://places.googleapis.com/v1/{photo_name}/media?maxHeightPx=800&key={api_key}"

FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.shortFormattedAddress",
    "places.location",
    "places.rating",
    "places.websiteUri",
    "places.nationalPhoneNumber",
    "places.currentOpeningHours.weekdayDescriptions",
    "places.addressComponents",
    "places.photos",
])

def to_decimal(val):
    try:
        return Decimal(str(val)) if val is not None else None
    except (InvalidOperation, TypeError, ValueError):
        return None

def trim(s, limit):
    if not s:
        return None
    s = str(s)
    return s[:limit] if len(s) > limit else s

def pick_city(address_components):
    """
    從 addressComponents 取城市：
    先找 locality，其次 administrative_area_level_1，再不行就 shortFormattedAddress 的前段。
    """
    if not address_components:
        return None
    # v1 types 仍包含類似 "locality"/"administrative_area_level_1"
    for comp in address_components:
        types = comp.get("types", [])
        name = comp.get("longText") or comp.get("shortText")
        if "locality" in types and name:
            return name
    for comp in address_components:
        types = comp.get("types", [])
        name = comp.get("longText") or comp.get("shortText")
        if "administrative_area_level_1" in types and name:
            return name
    return None

class Command(BaseCommand):
    help = "以 Google Places API v1 取得台灣熱門觀光景點，寫入 spots_spot"

    def add_arguments(self, parser):
        parser.add_argument("--lat", type=float, default=25.0330, help="中心點緯度（預設台北101）")
        parser.add_argument("--lng", type=float, default=121.5654, help="中心點經度（預設台北101）")
        parser.add_argument("--radius", type=float, default=50000.0, help="搜尋半徑（公尺）")
        parser.add_argument("--pages", type=int, default=1, help="抓取頁數（每頁最多20筆）")

    def handle(self, *args, **opts):
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            self.stderr.write(self.style.ERROR("缺少 GOOGLE_API_KEY 環境變數"))
            return

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": FIELD_MASK,
        }

        body = {
            "languageCode": "zh-TW",
            "regionCode": "TW",
            "includedTypes": ["tourist_attraction"],
            "maxResultCount": 20,
            "rankPreference": "POPULARITY",
            "locationRestriction": {
                "circle": {
                    "center": {
                        "latitude": float(opts["lat"]),
                        "longitude": float(opts["lng"]),
                    },
                    "radius": float(opts["radius"]),
                }
            },
        }

        saved, skipped = 0, 0
        next_page_token = None

        for _ in range(max(1, opts["pages"])):
            if next_page_token:
                body["pageToken"] = next_page_token

            resp = requests.post(PLACES_SEARCH_URL, json=body, headers=headers, timeout=30)
            if resp.status_code != 200:
                self.stderr.write(self.style.ERROR(f"API 錯誤 {resp.status_code}: {resp.text[:300]}"))
                break

            payload = resp.json()
            places = payload.get("places", []) or []
            self.stdout.write(f"本頁取得 {len(places)} 筆")

            for p in places:
                # 名稱（中文）
                name = (p.get("displayName") or {}).get("text") or ""
                if not name:
                    skipped += 1
                    continue
                if len(name) > 1000:
                    name = name[:1000]

                # 地址（中文）
                address = p.get("formattedAddress") or p.get("shortFormattedAddress") or None
                address = trim(address, 255)

                # 城市
                city = pick_city(p.get("addressComponents")) or None
                city = trim(city, 100)

                # 經緯度
                lat = to_decimal(((p.get("location") or {}).get("latitude")))
                lng = to_decimal(((p.get("location") or {}).get("longitude")))

                # 電話、網址、評分
                phone = trim(p.get("nationalPhoneNumber"), 20)
                website = trim(p.get("websiteUri"), 500)
                rating = p.get("rating", None)

                # 營業時間（JSONField，直接存 list）
                opening_hours = None
                current_hours = (p.get("currentOpeningHours") or {})
                weekday_desc = current_hours.get("weekdayDescriptions")
                if weekday_desc:
                    opening_hours = weekday_desc  # list[str]

                # 照片：取第一張
                photo_url = None
                photos = p.get("photos") or []
                if photos:
                    photo_name = photos[0].get("name")  # e.g. "places/xxx/photos/yyy"
                    if photo_name:
                        # 直接可用的圖片連結（Google 會回傳實體圖）
                        photo_url = PHOTO_MEDIA_URL_TMPL.format(photo_name=photo_name, api_key=api_key)
                        photo_url = trim(photo_url, 500)

                # place_id
                place_id = trim(p.get("id"), 255)

                obj, created = Spot.objects.get_or_create(
                    name=name,
                    defaults={
                        "address": address,
                        "city": city,
                        "latitude": lat,
                        "longitude": lng,
                        "phone": phone,
                        "url": website,
                        "rating": rating,
                        "place_id": place_id,
                        "opening_hours": opening_hours,
                        "description": None,  # v1 沒有 editorial_summary，可自行補抓 Place Details v1
                        "photo_url": photo_url,
                    },
                )
                if created:
                    saved += 1

            next_page_token = payload.get("nextPageToken")
            if not next_page_token:
                break
            # 官方建議等個 2 秒再拿下一頁
            time.sleep(2)

        self.stdout.write(self.style.SUCCESS(f"完成。新增 {saved} 筆，略過 {skipped} 筆"))
