import pyautogui
import sys

def close_active_tab():
    """Gửi phím nóng Ctrl+W để đóng tab hiện tại mà không tắt cả trình duyệt."""
    pyautogui.hotkey('ctrl', 'w')
    print("Đã gửi lệnh đóng tab hiện tại (Ctrl+W)")

if __name__ == '__main__':
    close_active_tab()
