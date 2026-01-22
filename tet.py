import re

def clean_hebrew_title(text):
    # 1. Remove common English technical tags
    text = re.sub(r'\b(1080p|720p|WEB-?DL|HDTV|WEB|x264|x265|HEVC)\b', '', text, flags=re.IGNORECASE)
    
    # 2. Remove Hebrew Uploader/Group tags (specific to your list)
    groups = ['זירה מדיה', 'ז\.מ', 'דב סרטים', 'שלמה סרטים', 'תוצרת קוריאה']
    for group in groups:
        text = re.sub(group, '', text)
        
    # 3. Extract Season/Episode info but remove it from the title search
    # Matches: ע1 פ1, עונה 1, פרק 1
    text = re.sub(r'ע(ונה)?\s?\d+', '', text)
    text = re.sub(r'פ(רק)?\s?\d+', '', text)
    
    # 4. Remove dashes, quotes and extra spaces
    text = text.replace('"', '').replace('-', ' ').strip()
    
    # 5. Clean up multiple spaces
    text = re.sub(r'\s+', ' ', text)
    if len(text) > 30:
        text = text[:30]
        if " " in text:
            text = text.rsplit(' ', 1)[0]
    return text

# Testing with your data
examples = [
    "1080p WEB זירה מדיה תוצרת קורי",
    "ז.מ החבר הקוריאני שלי ע1 פ5",
    "זגורי אימפריה ע1 פ3 דב סרטים HDTV",
    "שלמה סרטים מלבי אקספרס עונה  פרק 21",
    'מדברים על "המירוץ למיליון" • ספיר וליאור מוכנים למירוץ'
]

for ex in examples:
    print(f"Original: {ex}")
    print(f"Search Query: {clean_hebrew_title(ex)}\n")