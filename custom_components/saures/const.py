"""Constants for the Saures integration."""

DOMAIN = "saures"

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

# Update intervals
DEFAULT_UPDATE_INTERVAL = 300  # 5 minutes
BATTERY_UPDATE_INTERVAL = 3600  # 1 hour

# Update interval limits (in minutes)
MIN_UPDATE_INTERVAL = 1  # 1 minute
MAX_UPDATE_INTERVAL = 1440  # 24 hours 