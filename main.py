from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import pandas as pd
import math
import os
from enum import Enum

# 1. Настройка моделей данных
class Gender(str, Enum):
    male = "male"
    female = "female"

class Pool(str, Enum):
    scm = "SCM"
    lcm = "LCM"

app = FastAPI(title="World Aquatics Points API")

# 2. Загрузка базы данных
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "wa_base_times.csv")

# === Путь к файлу с разрядами ===
RANKS_PATH = os.path.join(BASE_DIR, "russwimming_ranks.csv")

try:
    df = pd.read_csv(CSV_PATH)
    # Приводим к нижнему регистру для надежного поиска
    df['Event_Search'] = df['Event'].str.lower()
    df['Pool_Search'] = df['Pool'].str.upper()
except FileNotFoundError:
    print(f"КРИТИЧЕСКАЯ ОШИБКА: Файл {CSV_PATH} не найден!")
    df = pd.DataFrame()

# === Загрузка российских нормативов ===
try:
    df_ranks = pd.read_csv(RANKS_PATH, sep=';')
    
    # === Функция безопасного парсинга времени нормативов ===
    def parse_rank_time(time_str):
        try:
            if pd.isna(time_str):
                return None
            time_str = str(time_str).strip().replace(',', '.')
            # Отсекаем некорректные данные (28.May, 30.Apr, 26.Mar и т.д.)
            if any(month in time_str for month in ['May', 'Apr', 'Mar', 'Jun', 'Jul', 'Aug']):
                return None
            if ":" in time_str:
                parts = time_str.split(":")
                if len(parts) == 2:
                    return int(parts[0]) * 60 + float(parts[1])
            return float(time_str)
        except:
            return None
    
    # === Конвертируем колонки времени в секунды ===
    df_ranks['Men_Sec'] = df_ranks['Men'].apply(parse_rank_time)
    df_ranks['Women_Sec'] = df_ranks['Women'].apply(parse_rank_time)
    
    # === Нормализуем названия для поиска ===
    df_ranks['Event_Search'] = df_ranks['Event (EN)'].str.lower().str.strip().apply(lambda x: " ".join(x.split()))
    df_ranks['Pool_Search'] = df_ranks['Pool'].str.upper().str.strip()
    
    # === Порядок разрядов (от высшего к низшему) ===
    RANK_ORDER = ['МСМК', 'МС', 'КМС', 'I', 'II', 'III', 'Iю', 'IIю', 'IIIю']
    
except FileNotFoundError:
    print(f"КРИТИЧЕСКАЯ ОШИБКА: Файл {RANKS_PATH} не найден!")
    df_ranks = pd.DataFrame()
    RANK_ORDER = []

# 3. Вспомогательная функция парсинга времени
def parse_time_to_seconds(time_str: str) -> float:
    """Конвертирует '1:05.42' или '65.42' в число 65.42"""
    try:
        # Убираем пробелы и меняем запятую на точку
        time_str = str(time_str).strip().replace(',', '.')
        
        if ":" in time_str:
            parts = time_str.split(":")
            if len(parts) == 2:
                minutes, seconds = parts
                return int(minutes) * 60 + float(seconds)
            else:
                raise ValueError("Неверный формат времени")
        
        return float(time_str)
    except Exception:
        raise HTTPException(status_code=400, detail="Неверный формат времени. Используйте СС.мс или М:СС.мс")

# === Функция определения разряда с информацией о следующем ===
def get_rank(pool: str, event: str, gender: str, swimmer_seconds: float) -> dict:
    if df_ranks.empty:
        return {
            "rank": "Нет данных", "rank_order": None, "norm_time": None,
            "time_to_norm": None, "next_rank": None, "seconds_to_next": None 
        }
    
    target_pool = pool.upper().strip()
    target_event = " ".join(event.lower().split())
    
    mask = (df_ranks['Pool_Search'] == target_pool) & \
           (df_ranks['Event_Search'] == target_event)
    
    ranks_data = df_ranks[mask]
    
    if ranks_data.empty:
        return {
            "rank": "Нет норматива", "rank_order": None, "norm_time": None,
            "time_to_norm": None, "next_rank": None, "seconds_to_next": None 
        }
    
    time_col = 'Men_Sec' if gender == 'male' else 'Women_Sec'
    
    achieved_rank = None
    achieved_index = None
    achieved_norm = None
    
    # Ищем достигнутый разряд
    for idx, rank in enumerate(RANK_ORDER):
        rank_row = ranks_data[ranks_data['Rank'] == rank]
        if not rank_row.empty:
            norm_time = rank_row[time_col].values[0]
            if norm_time is not None and swimmer_seconds <= norm_time:
                achieved_rank = rank
                achieved_index = idx
                achieved_norm = norm_time
                break
    
    # Если разряд не достигнут
    if achieved_rank is None:
        lowest_rank = RANK_ORDER[-1]
        lowest_row = ranks_data[ranks_data['Rank'] == lowest_rank]
        lowest_norm = lowest_row[time_col].values[0] if not lowest_row.empty else None
        
        return {
            "rank": "Без разряда",
            "rank_order": len(RANK_ORDER) + 1,
            "norm_time": None,
            "time_to_norm": round(lowest_norm - swimmer_seconds, 2) if lowest_norm else None,
            "next_rank": lowest_rank if lowest_norm else None, 
            "seconds_to_next": round(swimmer_seconds - lowest_norm, 2) if lowest_norm else None 
        }
    
    # === Ищем следующий высший разряд ===
    next_rank = None
    seconds_to_next = None
    
    if achieved_index > 0:  # Есть разряд выше
        next_rank_name = RANK_ORDER[achieved_index - 1]
        next_rank_row = ranks_data[ranks_data['Rank'] == next_rank_name]
        if not next_rank_row.empty:
            next_norm = next_rank_row[time_col].values[0]
            if next_norm is not None:
                next_rank = next_rank_name
                seconds_to_next = round(swimmer_seconds - next_norm, 2) 
    
    return {
        "rank": achieved_rank,
        "rank_order": achieved_index + 1,
        "norm_time": achieved_norm,
        "time_to_norm": round(achieved_norm - swimmer_seconds, 2),
        "next_rank": next_rank, 
        "seconds_to_next": seconds_to_next 
    }

# 4. Эндпоинты API
@app.get("/events")
def get_events(pool: str = None):
    """
    Возвращает список всех уникальных дистанций.
    Можно отфильтровать по типу бассейна: ?pool=SCM или ?pool=LCM
    """
    if pool:
        events = df[df['Pool'] == pool.upper()]['Event'].unique().tolist()
    else:
        events = df['Event'].unique().tolist()
    return {"count": len(events), "events": sorted(events)}

# Эндпоинт для просмотра нормативов ===
@app.get("/ranks")
def get_ranks(pool: str = None, event: str = None):
    """
    Возвращает все нормативы для указанной дистанции и бассейна
    """
    if df_ranks.empty:
        raise HTTPException(status_code=500, detail="База нормативов не загружена")
    
    result = df_ranks.copy()
    
    if pool:
        result = result[result['Pool'] == pool.upper()]
    
    if event:
        target_event = " ".join(event.lower().split())
        result = result[result['Event_Search'] == target_event]
    
    if result.empty:
        raise HTTPException(status_code=404, detail="Нормативы не найдены")
    
    return {
        "count": len(result),
        "ranks": result[['Pool', 'Event (EN)', 'Event (RU)', 'Men', 'Women', 'Rank']].to_dict('records')
    }

@app.get("/calculate")
def calculate(time: str, event: str, pool: str, gender: Gender):
    """
    Выполняет расчет очков World Aquatics и определяет спортивный разряд по заданным параметрам.

    Параметры:
    - time: время пловца (например, 01:49.22 или 109.22)
    - event: дистанция (например, 100m Breaststroke)
    - pool: тип бассейна ('SCM' или 'LCM')
    - gender: пол ('male' или 'female')
    """
    # 1. Конвертируем строку времени в секунды (float)
    swimmer_seconds = parse_time_to_seconds(time)

    # 2. Нормализуем входящие данные для поиска
    # Убираем лишние пробелы внутри строки и по краям, приводим к нижнему регистру
    target_event = " ".join(event.lower().split())
    target_pool = pool.upper().strip()

    # 3. Ищем в базе с такой же нормализацией
    # Мы создаем маску, которая чистит значения в столбцах Event и Pool перед сравнением
    mask = (df['Pool'].str.upper().str.strip() == target_pool) & \
           (df['Event'].str.lower().str.strip().apply(lambda x: " ".join(x.split())) == target_event)

    row = df[mask]

    if row.empty:
        # Для отладки в консоли Uvicorn
        print(f"Ошибка поиска: Pool='{target_pool}', Event='{target_event}'")
        raise HTTPException(
            status_code=404, 
            detail=f"Дистанция '{event}' не найдена в базе для бассейна {target_pool}"
        )

    # 4. Выбираем базовое время (берем первый найденный результат .values[0])
    try:
        if gender == Gender.male:
            base_time = float(row['Men_Base'].values[0])
        else: 
            base_time = float(row['Women_Base'].values[0])
    except (IndexError, ValueError):
        raise HTTPException(status_code=500, detail="Ошибка данных в базе для этой дистанции")

    # 5. Формула: P = 1000 * (Base / Time) ^ 3
    # math.floor всегда округляет вниз до целого
    points = math.floor(1000 * (base_time / swimmer_seconds) ** 3)

    rank_info = get_rank(target_pool, target_event, gender.value, swimmer_seconds)

    return {
        "event": event,
        "pool": target_pool,
        "gender": gender.value,
        "input_time": time,
        "seconds": swimmer_seconds,
        "base_time": base_time,
        "points": max(0, points),
        "rank": rank_info["rank"],
        "rank_order": rank_info["rank_order"],
        "norm_time": rank_info.get("norm_time"),
        "time_to_norm": rank_info.get("time_to_norm"),
        "next_rank": rank_info.get("next_rank"),  
        "seconds_to_next": rank_info.get("seconds_to_next") 
    }

# @app.get("/")
# def read_root():
#     return {"message": "Система расчета очков World Aquatics готова к работе"}

# 5. Раздача фронтенда (HTML)
# Этот блок должен быть в конце, чтобы не перехватывать API-запросы
@app.get("/")
async def read_index():
    index_path = os.path.join(BASE_DIR, "static", "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return {"error": "index.html не найден в папке static"}

# Монтируем статику для CSS/JS (если они будут в отдельных файлах)
if os.path.exists(os.path.join(BASE_DIR, "static")):
    app.mount("/static", StaticFiles(directory="static"), name="static")