import serial
import time
import sys

# См. боевой модуль (без serial в ядре): neurosync_pro.eeg.tgam_serial_parser
# и PoC: блок «TGAM COM (ThinkGear, исслед.)» в meditation_poc.

# --- КОНСТАНТЫ ПРОТОКОЛА TGAM ---
SYNC = 0xAA
EXCODE = 0x55
CODE_RAW = 0x80
CODE_POOR_SIGNAL = 0x02
CODE_ATTENTION = 0x04
CODE_MEDITATION = 0x05
CODE_BLINK = 0x16

class BrainLinkParser:
    def __init__(self):
        self.reset()

    def reset(self):
        self.payload = []
        self.payload_length = 0
        self.state = 'SYNC'
        # Данные для агента
        self.signal = 200
        self.attention = 0
        self.meditation = 0
        self.blink = 0
        self.raw_wave = 0
        self.new_data_available = False

    def process_byte(self, byte):
        byte = byte & 0xFF
        
        if self.state == 'SYNC':
            if byte == SYNC:
                self.state = 'SYNC_CHECK'
        
        elif self.state == 'SYNC_CHECK':
            if byte == SYNC:
                self.state = 'PAYLOAD_LENGTH'
            else:
                self.state = 'SYNC' # Сбой, ищем заново
        
        elif self.state == 'PAYLOAD_LENGTH':
            self.payload_length = byte
            self.payload = []
            if self.payload_length > 0 and self.payload_length < 170:
                self.state = 'PAYLOAD'
            else:
                self.state = 'SYNC' # Неверная длина, сброс
        
        elif self.state == 'PAYLOAD':
            self.payload.append(byte)
            if len(self.payload) >= self.payload_length:
                self.state = 'CHECKSUM'
        
        elif self.state == 'CHECKSUM':
            received_checksum = byte
            # ФОРМУЛА ИЗ НАЙДЕННОГО КОДА: Инверсия суммы!
            calculated_checksum = (~(sum(self.payload) & 0xFF)) & 0xFF
            
            if calculated_checksum == received_checksum:
                self.decode_payload()
                self.new_data_available = True # Флаг: новые данные готовы!
            else:
                # Ошибка суммы (редко)
                pass
            
            self.state = 'SYNC' # Всегда возвращаемся к поиску синхронизации

    def decode_payload(self):
        i = 0
        while i < len(self.payload):
            code = self.payload[i]
            i += 1
            
            if code == EXCODE:
                continue
            
            if i >= len(self.payload):
                break
                
            value = self.payload[i]
            
            if code == CODE_POOR_SIGNAL:
                self.signal = value
                # print(f"[SIGNAL] {self.signal}")
            
            elif code == CODE_ATTENTION:
                self.attention = value
                # print(f"[ATTENTION] {self.attention}")
            
            elif code == CODE_MEDITATION:
                self.meditation = value
                # print(f"[MEDITATION] {self.meditation}")
            
            elif code == CODE_BLINK:
                self.blink = value
                print(f"\n👁️ BLINK DETECTED: {self.blink}")
            
            elif code == CODE_RAW:
                # Raw wave может быть многобайтовым
                if i + 1 < len(self.payload):
                    # Обычно 2 байта для raw в некоторых версиях, или больше
                    # В простейшем случае берем следующий байт как часть
                    # Но в стандартном TGAM raw идет блоком. 
                    # Для простоты пока пропустим детальную обработку raw, 
                    # так как нам важнее Attention/Meditation для агента.
                    pass
            
            else:
                # Пропуск неизвестных мультибайтовых кодов
                if code >= 0x80:
                    if i < len(self.payload):
                        length = self.payload[i]
                        i += length
            
            i += 1

    def get_data(self):
        if self.new_data_available:
            self.new_data_available = False
            return {
                'signal': self.signal,
                'attention': self.attention,
                'meditation': self.meditation,
                'blink': self.blink
            }
        return None

# --- ОСНОВНОЙ ЦИКЛ ---
PORT = 'COM3'
BAUDRATE = 57600

print("🧠 ЗАПУСК MASTER PARSER (Исправленная контрольная сумма)")
print("Ожидание данных от BrainLink Pro...\n")

try:
    ser = serial.Serial(PORT, BAUDRATE, timeout=0.1)
    time.sleep(1)
    ser.reset_input_buffer()
    
    parser = BrainLinkParser()
    
    last_print_time = 0

    while True:
        if ser.in_waiting > 0:
            byte = ser.read(1)[0]
            parser.process_byte(byte)
            
            data = parser.get_data()
            if data:
                # Выводим данные не чаще раза в 0.5 сек, чтобы не спамить
                current_time = time.time()
                if current_time - last_print_time > 0.5:
                    print(f"📶 Sig: {data['signal']} | 🎯 Att: {data['attention']} | 🧘 Med: {data['meditation']} | 👁️ Blink: {data['blink']}")
                    last_print_time = current_time
                    
                    # Проверка на хорошие данные для агента
                    if data['signal'] == 0 and data['attention'] > 80:
                        print("   >>> СОСТОЯНИЕ: ВЫСОКАЯ КОНЦЕНТРАЦИЯ!")
                    elif data['signal'] == 0 and data['meditation'] > 80:
                        print("   >>> СОСТОЯНИЕ: ГЛУБОКАЯ МЕДИТАЦИЯ!")

except KeyboardInterrupt:
    print("\n\n👋 Остановка. Удачи в создании Агента!")
    if 'ser' in locals(): ser.close()
except Exception as e:
    print(f"\n❌ Ошибка: {e}")