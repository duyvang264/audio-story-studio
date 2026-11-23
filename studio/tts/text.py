"""Pronunciation-only substitutions; retain original text for subtitles and continuity."""
import re
import unicodedata

DIGITS=['không','một','hai','ba','bốn','năm','sáu','bảy','tám','chín']

def number(value: int) -> str:
    if value<10:return DIGITS[value]
    if value<20:return 'mười'+(' '+('lăm' if value==15 else DIGITS[value%10]) if value%10 else '')
    if value<100:
        unit=value%10
        return DIGITS[value//10]+' mươi'+(' '+('mốt' if unit==1 else 'lăm' if unit==5 else DIGITS[unit]) if unit else '')
    if value<1000:
        rest=value%100
        return DIGITS[value//100]+' trăm'+(' '+('linh '+DIGITS[rest] if rest<10 else number(rest)) if rest else '')
    if value<10000:
        rest=value%1000
        return DIGITS[value//1000]+' nghìn'+(' '+('không trăm '+('linh '+DIGITS[rest] if rest<10 else number(rest)) if rest<100 else number(rest)) if rest else '')
    return ' '.join(DIGITS[int(d)] for d in str(value))


def spoken_text(text: str, overrides: dict[str,str] | None=None) -> tuple[str,list[str]]:
    text=unicodedata.normalize('NFC',text)
    for key in sorted(overrides or {},key=len,reverse=True):text=text.replace(key,overrides[key])
    text=re.sub(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b',lambda m:'ngày '+number(int(m[1]))+' tháng '+number(int(m[2]))+' năm '+number(int(m[3])),text)
    text=re.sub(r'\b([01]?\d|2[0-3]):([0-5]\d)\b',lambda m:number(int(m[1]))+' giờ '+number(int(m[2]))+' phút',text)
    issues=re.findall(r'\b(?=[A-Z0-9_]*[A-Z])(?=[A-Z0-9_]*\d)[A-Z0-9_]{6,}\b',text)
    return text,issues
