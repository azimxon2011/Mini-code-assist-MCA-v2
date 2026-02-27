import google.generativeai as genai

GEMINI_API_KEY = "AIzaSyDze9Z5aCrxJR9G5YmpcpvNXOOeAgEmK6s"
genai.configure(api_key=GEMINI_API_KEY)

SYSTEM_INSTRUCTION = (
    "Ты — ученик 8-го класса на олимпиаде по программированию. Тебе лень, ты пишешь просто и быстро.\n"
    "Твои правила:\n"
    "1. Пиши простой код, как восьмиклассник. Не парься над оптимизацией. В коде не должно быть ООП или сложных структур.\n"
    "2. Используй простые имена переменных: a, b, x, y, s, прочие имена что встречаються в задаче.\n"
    "3. Никаких импортов.\n"
    "4. Возвращай ТОЛЬКО код. Без markdown, без лишних пояснений.\n"
    "5. Дедукция: если нужно уодноразно вернуться на уровень назад (после цикла/блока), используй любой Backspace через \\x08."
)

model = genai.GenerativeModel(
    model_name="gemini-2.5-flash",
    system_instruction=SYSTEM_INSTRUCTION
)

tasks = [
    "Напиши программу, которая находит самое длинное слово в введенной строке.",
    "Напиши код, который проверяет, является ли число простым.",
    "Напиши программу для сортировки массива чисел по убыванию без встроенных функций sort/sorted."
]

for i, task in enumerate(tasks):
    print(f"--- ЗАДАЧА {i+1} ---")
    print(task)
    response = model.generate_content(task)
    print("\n--- РЕЗУЛЬТАТ ---")
    print(response.text)
    print("\n" + "="*40 + "\n")
