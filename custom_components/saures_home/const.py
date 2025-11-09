"""Constants for the Saures integration."""

DOMAIN = "saures_home"

# API settings
API_URL = "https://api.saures.ru/1.0"
REQUEST_ATTEMPTS = 5

# Device types from API
DEVICE_TYPES = {
    1: "Холодная вода",
    2: "Горячая вода",
}

# Device states from API
DEVICE_STATES = {
    0: "Ошибок нет",
    1: "Обрыв",
    2: "Короткое замыкание", 
    3: "Перерасход ресурса в течение заданного промежутка времени",
    4: "Температура/давление опустились ниже нижнего порога",
    5: "Температура/давление поднялись выше верхнего порога",
    6: "Остановка потребления ресурса в течение заданного промежутка времени",
    7: "Ошибка связи или внутренняя неисправность счетчика с цифровым интерфейсом (RS-485/CAN/MBUS)",
    8: "Некорректное значение или выход значения за пределы измеряемого диапазона",
    9: "Воздействие магнитного поля",
    10: "Обратный поток жидкости",
}

# Update intervals (in seconds)
DEFAULT_UPDATE_INTERVAL = 3600  # 60 minutes (1 hour - recommended to prevent API rate limiting)
BATTERY_UPDATE_INTERVAL = 3600  # 1 hour
STATISTICS_IMPORT_INTERVAL = 86400  # 24 hours (once per day)

# Update interval limits (in minutes)
MIN_UPDATE_INTERVAL = 60  # 60 minutes (1 hour - to prevent API rate limiting and bans)
MAX_UPDATE_INTERVAL = 1440  # 24 hours
DEFAULT_UPDATE_INTERVAL_MINUTES = 60  # Default interval in minutes for config flow (1 hour recommended) 