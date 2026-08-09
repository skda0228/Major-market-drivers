import json
import os
import datetime
import zoneinfo
import subprocess
import re
import sys

try:
    import yfinance as yf
except ImportError:
    print("yfinance 라이브러리가 필요합니다. pip install yfinance 로 설치해주세요.")
    sys.exit(1)

try:
    from google import genai
except ImportError:
    genai = None

# 1. 티커 매핑
TICKER_MAP = {
    "KOSPI": "^KS11",
    "SPX": "^GSPC",
    "IXIC": "^IXIC",
    "DJI": "^DJI",
    "N225": "^N225",
    "US10Y": "^TNX",
    "VIX": "^VIX",
    "USD/KRW": "KRW=X",
    "WTI": "CL=F",
    "XAU/USD": "GC=F",
    "SHCOMP": "000001.SS",
    "HSI": "^HSI",
    "VNI": "^VNINDEX.VN",
    "SX5E": "^STOXX50E",
    "UKX": "^FTSE",
    "DAX": "^GDAXI",
    "SENSEX": "^BSESN"
}

def get_latest_price(ticker_symbol):
    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period="1d")
        if df.empty:
            return None, None, None, None
        
        # [버그 2 해결] 이 종가의 실제 최신 거래일 추출
        actual_date_str = df.index[-1].strftime("%Y-%m-%d")
        
        close_price = df['Close'].iloc[-1]
        prev_close = ticker.info.get('previousClose')
        
        if prev_close is None:
            df2 = ticker.history(period="5d")
            if len(df2) > 1:
                prev_close = df2['Close'].iloc[-2]
            else:
                prev_close = close_price
                
        change = close_price - prev_close
        change_pct = (change / prev_close) * 100 if prev_close else 0
        
        return close_price, change, change_pct, actual_date_str
    except Exception as e:
        print(f"Error fetching {ticker_symbol}: {e}")
        return None, None, None, None

def format_value(code, value):
    if code in ["US10Y"]:
        return f"{value:.3f}%"
    elif code in ["VIX", "WTI"]:
        return f"{value:.2f}"
    else:
        return f"{value:,.2f}"

def get_dir(code, change_pct):
    if code in ["US10Y", "VIX", "WTI", "XAU/USD", "USD/KRW"]:
        return "nt"
    if change_pct > 0:
        return "up"
    elif change_pct < 0:
        return "dn"
    return "nt"

def generate_ai_analysis(market_name, change_pct):
    if not genai or not os.environ.get("GEMINI_API_KEY"):
        return f"AI 분석 생략 (API 키 없음). {market_name} 변동: {change_pct:.2f}%"
    
    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        prompt = f"오늘 {market_name} 지수가 {change_pct:.2f}% 급변했습니다. 금융 뉴스 헤드라인을 참고해서 원인을 1문장으로 요약해주세요."
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        return response.text.strip()
    except Exception as e:
        return f"AI 분석 실패: {e}"

def update_html_dates(kst, last_trade_full, last_trade_str):
    html_files = ["index.html", "semiconductor.html", "credit-balance.html"]
    today_dot = datetime.datetime.now(kst).strftime("%Y.%m.%d %H:%M")
    today_just_date = today_dot[:10]
    
    for html_file in html_files:
        if os.path.exists(html_file):
            with open(html_file, 'r', encoding='utf-8') as f:
                content = f.read()
            
            new_content = content
            new_content = re.sub(r'<span class="rd">\d{4}\.\d{2}\.\d{2}(?: \d{2}:\d{2})?</span>', f'<span class="rd">{today_dot}</span>', new_content)
            new_content = re.sub(r'마지막 거래일 <b>\d{4}\.\d{2}\.\d{2}\([월화수목금토일]\)</b>', f'마지막 거래일 <b>{last_trade_full}</b>', new_content)
            new_content = re.sub(r'<div class="ds">\d{4}\.\d{2}\.\d{2} CLOSE', f'<div class="ds">{last_trade_str} CLOSE', new_content)
            new_content = re.sub(r'다음 관문 · \d{4}\.\d{2}\.\d{2} 기준', f'다음 관문 · {today_just_date} 기준', new_content)

            if new_content != content:
                with open(html_file, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                print(f"{html_file} 날짜 완벽 연동 업데이트 완료!")

def main():
    json_file = "indicators.json"
    if not os.path.exists(json_file):
        print(f"{json_file} 파일을 찾을 수 없습니다.")
        return

    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    kst = zoneinfo.ZoneInfo("Asia/Seoul")
    today_str = datetime.datetime.now(kst).strftime("%Y-%m-%d")

    big_change_notes = []
    
    # [버그 4 방어] 하나라도 실제로 갱신된 지표가 있는지 추적
    any_updates_made = False
    
    asof_candidates = [data.get("asOf", "2000-01-01")]

    print(f"=== 실행 시작 (서버시간: {today_str}) ===")

    # 1. 핵심 지표 업데이트
    for item in data.get("items", []):
        code = item.get("code")
        symbol = TICKER_MAP.get(code)
        if not symbol:
            continue
            
        print(f"[{code}] 확인 중...")
        price, change, change_pct, actual_date = get_latest_price(symbol)
        
        if price is None or actual_date is None:
            continue
            
        last_saved_date = item.get("lastDate", "2000-01-01")
        
        # [버그 2 & 3 해결] 멱등성(Idempotency) 체크
        if actual_date > last_saved_date:
            print(f"  -> 🟢 새로운 거래일 감지 ({last_saved_date} -> {actual_date})! 갱신 진행.")
            is_new_day = True
        elif actual_date == last_saved_date:
            print(f"  -> 🟡 같은 거래일({actual_date}). 장중 갱신(마지막 값 덮어쓰기)만 수행.")
            is_new_day = False
        else:
            print(f"  -> 🔴 과거 거래일({actual_date} <= {last_saved_date}). 업데이트 완전히 스킵.")
            continue # 데이터가 낡았거나 동일하면 완전히 스킵 (WTI/금 Jitter 방지)
            
        any_updates_made = True
        asof_candidates.append(actual_date)
        item["lastDate"] = actual_date
        
        if abs(change_pct) >= 3.0:
            print(f"!!! {item['name']} 급변 감지 ({change_pct:.2f}%) -> AI 분석 호출")
            note = generate_ai_analysis(item['name'], change_pct)
            item["note"] = note
            big_change_notes.append(f"{item['name']}: {note}")

        item["value"] = format_value(code, price)
        sym = "▲" if change > 0 else "▼" if change < 0 else "-"
        val_fmt = f"{abs(change):.3f}%p" if code == "US10Y" else f"{abs(change):.2f}"
        item["change"] = f"{sym} {val_fmt} ({change_pct:+.2f}%)"
        item["dir"] = get_dir(code, change_pct)
        
        val = float(round(price, 2))
        
        # [버그 1 해결] 일간/월간 배열 회전 및 길이 유지 로직
        if "q" in item:
            if is_new_day or len(item["q"]) == 0:
                item["q"].append(val)
                # 길이 초과 시 맨 앞 데이터 제거 (UPDATE.md 규정: 21~23개 유지)
                while len(item["q"]) > 23:
                    item["q"].pop(0)
            else:
                item["q"][-1] = val # 새 거래일이 아니면 무한 증식 방지(덮어쓰기)
                
        if "y" in item:
            actual_month = actual_date[:7] # YYYY-MM 추출
            last_saved_month = last_saved_date[:7]
            
            if actual_month > last_saved_month or len(item["y"]) == 0:
                item["y"].append(val)
                while len(item["y"]) > 12:
                    item["y"].pop(0)
            else:
                item["y"][-1] = val

    # 2. 기타 지표 업데이트
    for other in data.get("others", []):
        code = other.get("code")
        symbol = TICKER_MAP.get(code)
        if not symbol:
            continue
            
        price, change, change_pct, actual_date = get_latest_price(symbol)
        if price is None or actual_date is None:
            continue
            
        last_saved_date = other.get("lastDate", "2000-01-01")
        if actual_date >= last_saved_date: # 새롭거나 같은 날일 때만
            if actual_date > last_saved_date:
                asof_candidates.append(actual_date)
            other["lastDate"] = actual_date
            other["value"] = format_value(code, price)
            other["change"] = f"{change_pct:+.2f}%"
            other["dir"] = get_dir(code, change_pct)
            any_updates_made = True

    # [부가 문제 해결] 신규 갱신이 없으면 여기서 스크립트 완전 종료 (거짓 신선도 방지)
    if not any_updates_made:
        print("💡 새롭게 갱신된 데이터가 없습니다. (주말/휴장일) - JSON 및 HTML 파일 수정 없이 종료합니다.")
        return

    # [버그 2 해결] asOf는 "오늘 스크립트 실행 날짜"가 아니라 "실제 거래일 중 가장 최신 날짜"
    final_asof = max(asof_candidates)
    data["asOf"] = final_asof

    # JSON 저장
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        
    print(f"JSON 업데이트 완료! (asOf: {final_asof})")
    
    # 마지막 거래일 자동 추출 (HTML용)
    last_trade_str = final_asof.replace("-", ".")
    try:
        dt = datetime.datetime.strptime(final_asof, "%Y-%m-%d")
        weekdays = ["월", "화", "수", "목", "금", "토", "일"]
        last_trade_day = weekdays[dt.weekday()]
        last_trade_full = f"{last_trade_str}({last_trade_day})"
    except Exception:
        last_trade_full = last_trade_str

    update_html_dates(kst, last_trade_full, last_trade_str)

    # 3. Git 자동 커밋 & 푸시 (예외 처리 강화)
    try:
        subprocess.run(["git", "add", "indicators.json", "index.html", "semiconductor.html", "credit-balance.html"], check=True)
        result = subprocess.run(["git", "diff", "--staged"], capture_output=True, text=True)
        if result.stdout.strip():
            commit_msg = f"Auto update: {final_asof}"
            if big_change_notes:
                commit_msg += "\n\n" + "\n".join(big_change_notes)
            subprocess.run(["git", "commit", "-m", commit_msg], check=True)
            subprocess.run(["git", "push"], check=True)
            print(f"Git 푸시까지 완벽하게 완료되었습니다! ({final_asof})")
        else:
            print("Git 변경사항이 없어 커밋을 건너뜁니다.")
    except Exception as e:
        print(f"Git 작업 중 오류 발생: {e}")

if __name__ == "__main__":
    main()
	
