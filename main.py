import time
import smbus
import RPi.GPIO as GPIO
import logging

import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

SERVICE_ACCOUNT_FILE = '/home/gauthier/project/credentials.json'
SPREADSHEET_ID = '1ofAFZ7zbsBMsbVqPqv0Tpz04eEuTAR5DM1iMniClTIs'  
RANGE = 'Sheet1!A:B' 

I2C_ADDR = 0x27
I2C_BUS = 1

ROW_PINS = [17, 27, 22, 5]
COL_PINS = [6, 23, 24]

MASK_RS = 0x01
MASK_RW = 0x02
MASK_EN = 0x04
MASK_BACKLIGHT = 0x08
DATA_SHIFT = 4

class PCF8574:
    def __init__(self, address, busnum=I2C_BUS):
        self.bus = smbus.SMBus(busnum)
        self.addr = address
        self.backlight = MASK_BACKLIGHT
        self._value = 0
        self.write(0)

    def write(self, byte):
        self._value = byte
        self.bus.write_byte(self.addr, byte)

    def read(self):
        return self.bus.read_byte(self.addr)

class LCD_I2C:
    def __init__(self, address=I2C_ADDR):
        self.pcf = PCF8574(address)
        self._bl = MASK_BACKLIGHT

    def _pulse(self, data):
        self.pcf.write(data | MASK_EN)
        time.sleep(0.0005)
        self.pcf.write(data & ~MASK_EN)
        time.sleep(0.0001)

    def _write4(self, nibble, rs):
        data = ((nibble & 0x0F) << DATA_SHIFT)
        if rs:
            data |= MASK_RS
        data |= self._bl
        self._pulse(data)

    def _send(self, byte, rs):
        self._write4((byte >> 4) & 0x0F, rs)
        self._write4(byte & 0x0F, rs)

    def command(self, cmd):
        self._send(cmd, 0)

    def write_char(self, ch):
        self._send(ord(ch), 1)

    def init(self):
        time.sleep(0.05)
        self._write4(0x03, 0); time.sleep(0.005)
        self._write4(0x03, 0); time.sleep(0.0002)
        self._write4(0x03, 0); time.sleep(0.0002)
        self._write4(0x02, 0)
        self.command(0x28)
        self.command(0x08)
        self.command(0x01)
        time.sleep(0.002)
        self.command(0x06)
        self.command(0x0C)

    def clear(self):
        self.command(0x01)
        time.sleep(0.002)

    def set_cursor(self, line, pos=0):
        addr = 0x80 + (0x40 if line == 1 else 0x00) + pos
        self.command(addr)

    def write(self, text, line=0):
        if "\n" in text:
            lines = text.split("\n", 1)
            self.clear()
            self.set_cursor(0, 0)
            for ch in lines[0][:16]:
                self.write_char(ch)
            self.set_cursor(1, 0)
            for ch in lines[1][:16]:
                self.write_char(ch)
            return
        self.clear()
        self.set_cursor(line, 0)
        for ch in str(text)[:16]:
            self.write_char(ch)

    def set_backlight(self, on=True):
        self._bl = MASK_BACKLIGHT if on else 0
        self.pcf.write(self._bl)

class Keypad:
    KEYMAP = [
        ['1','2','3'],
        ['4','5','6'],
        ['7','8','9'],
        ['*','0','#']
    ]

    def __init__(self, rows=ROW_PINS, cols=COL_PINS):
        self.rows = rows
        self.cols = cols
        GPIO.setmode(GPIO.BCM)
        for r in self.rows:
            GPIO.setup(r, GPIO.OUT, initial=GPIO.HIGH)
        for c in self.cols:
            GPIO.setup(c, GPIO.IN, pull_up_down=GPIO.PUD_UP)

    def get_key(self, timeout=None):
        start = time.time()
        while True:
            for i, r in enumerate(self.rows):
                GPIO.output(r, GPIO.LOW)
                for j, c in enumerate(self.cols):
                    if GPIO.input(c) == GPIO.LOW:
                        time.sleep(0.02)
                        if GPIO.input(c) == GPIO.LOW:
                            key = self.KEYMAP[i][j]
                            while GPIO.input(c) == GPIO.LOW:
                                time.sleep(0.01)
                            GPIO.output(r, GPIO.HIGH)
                            return key
                GPIO.output(r, GPIO.HIGH)
            if timeout and (time.time() - start) > timeout:
                return None
            time.sleep(0.01)

    def cleanup(self):
        GPIO.cleanup()

def sheets_insert(id):
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=['https://www.googleapis.com/auth/spreadsheets']
    )
    service = build('sheets', 'v4', credentials=creds)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    body = {'values': [[str(id), now]]}
    result = service.spreadsheets().values().append(
        spreadsheetId=SPREADSHEET_ID,
        range=RANGE,
        valueInputOption='USER_ENTERED',
        insertDataOption='INSERT_ROWS',
        body=body
    ).execute()

logging.basicConfig(filename='error.log', level=logging.ERROR)

def main():
    lcd = LCD_I2C(I2C_ADDR)
    lcd.init()
    keypad = Keypad()
    last_active = time.time()
    backlight_on = True

    try:
        while True:
            lcd.set_backlight(True)
            backlight_on = True
            lcd.write("Entrez Numero...\n*:Sup #:Val")
            pin = ""
            last_active = time.time()
            while True:
                key = keypad.get_key(timeout=0.5)
                now = time.time()
                if key is not None:
                    last_active = now
                    if not backlight_on:
                        lcd.set_backlight(True)
                        backlight_on = True
                if key is None:
                    if backlight_on and (now - last_active > 60):
                        lcd.set_backlight(False)
                        backlight_on = False
                    continue
                if key == '*':
                    pin = pin[:-1]
                elif key == '#':
                    lcd.write(f"Confirmer? {pin}\n*:Non #:Oui")
                    while True:
                        confirm_key = keypad.get_key(timeout=0.5)
                        if confirm_key == '#':
                            lcd.write("Envoi...", 0)
                            try:
                                sheets_insert(pin)
                                lcd.write("Termine", 0)
                            except Exception as e:
                                lcd.write("Erreur", 0)
                                logging.error("Sheets insert failed: %s", str(e))
                                time.sleep(2)
                            else:
                                time.sleep(1.5)
                            break
                        elif confirm_key == '*':
                            break
                    if confirm_key == '#':
                        break
                elif key.isdigit() and len(pin) < 4:
                    pin += key

                display = pin + ("_" * (4 - len(pin)))
                lcd.write(f"Numero: {display}\n*:Sup #:Val", 0)

    except KeyboardInterrupt:
        pass
    finally:
        lcd.clear()
        lcd.set_backlight(False)
        keypad.cleanup()

if __name__ == "__main__":
    main()