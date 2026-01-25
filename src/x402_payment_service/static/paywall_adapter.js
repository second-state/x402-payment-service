(function() {
  'use strict';
  const TOKEN = window.__x402_token || { symbol: 'TOKEN', address: '', decimals: 18, name: '', version: '1' };
  const DISPLAY_AMOUNT = window.__x402_display_amount || 0;
  const PERMIT_DEADLINE = 3600;

  if (!TOKEN.address) return;

  if (window.x402?.paymentRequirements?.[0]?.extra) {
    const extra = window.x402.paymentRequirements[0].extra;
    if (extra.name) TOKEN.name = extra.name;
    if (extra.version) TOKEN.version = extra.version;
  }

  function formatAmount(atomic, decimals) {
    const d = BigInt(10) ** BigInt(decimals);
    const n = BigInt(atomic);
    const i = (n / d).toString();
    const f = (n % d).toString().padStart(decimals, '0').slice(0, 4).replace(/0+$/, '');
    return f ? `${i}.${f}` : i;
  }

  async function getBalance() {
    if (!window.ethereum) return null;
    try {
      const accounts = await window.ethereum.request({ method: 'eth_accounts' });
      if (!accounts?.length) return null;
      const data = '0x70a08231' + accounts[0].slice(2).padStart(64, '0');
      const result = await window.ethereum.request({ method: 'eth_call', params: [{ to: TOKEN.address, data }, 'latest'] });
      return formatAmount(result, TOKEN.decimals);
    } catch { return null; }
  }

  async function updateBalance() {
    const bal = await getBalance();
    if (bal === null) return;
    document.querySelectorAll('[data-x402-balance]').forEach(el => {
      el.textContent = `${bal} ${TOKEN.symbol}`;
    });
  }

  function replaceText() {
    const amountStr = `${DISPLAY_AMOUNT} ${TOKEN.symbol}`;
    document.querySelectorAll('h1, h2, h3, p, span, div, button, a').forEach(el => {
      if (el.children.length === 0 && el.textContent) {
        let t = el.textContent;
        t = t.replace(/\$[\d.]+\s*USD/gi, amountStr);
        t = t.replace(/[\d.]+\s*USDC/gi, amountStr);
        t = t.replace(/USDC/g, TOKEN.symbol);
        if (t !== el.textContent) el.textContent = t;
      }
    });
    document.querySelectorAll('[data-x402-amount]').forEach(el => {
      el.textContent = amountStr;
    });
    document.querySelectorAll('[data-x402-balance]').forEach(el => {
      if (!el.textContent || el.textContent.includes('USDC') || el.textContent.includes('USD')) {
        el.textContent = `-- ${TOKEN.symbol}`;
      }
    });
  }

  let processing = false, debounce = null, ready = false;

  function update() {
    if (processing) return;
    processing = true;
    try { replaceText(); } finally { processing = false; }
  }

  function init() {
    update();
    ready = true;
    setTimeout(updateBalance, 1000);
  }

  document.readyState === 'loading'
    ? document.addEventListener('DOMContentLoaded', init)
    : init();

  new MutationObserver((m) => {
    if (!ready || processing) return;
    if (m.some(x => x.type === 'childList' && x.addedNodes.length)) {
      clearTimeout(debounce);
      debounce = setTimeout(update, 150);
    }
  }).observe(document.body, { childList: true, subtree: true });

  if (window.ethereum) {
    const origRequest = window.ethereum.request.bind(window.ethereum);
    window.ethereum.request = async function(args) {
      if (args.method === 'eth_signTypedData_v4') {
        try {
          const data = JSON.parse(args.params[1]);
          if (data?.primaryType === 'TransferWithAuthorization') {
            const domain = data.domain || {};
            const msg = data.message || {};
            const owner = args.params[0];
            const deadline = Math.floor(Date.now() / 1000) + PERMIT_DEADLINE;
            const permitDomain = {
              name: TOKEN.name || domain.name,
              version: TOKEN.version || domain.version || '1',
              chainId: domain.chainId,
              verifyingContract: TOKEN.address,
            };
            const nonceData = '0x7ecebe00' + owner.slice(2).padStart(64, '0');
            const nonceHex = await origRequest({ method: 'eth_call', params: [{ to: TOKEN.address, data: nonceData }, 'latest'] });
            const nonce = parseInt(nonceHex, 16);
            const permitMsg = {
              owner,
              spender: msg.to || msg.recipient,
              value: msg.value,
              nonce,
              deadline: String(deadline),
            };
            const newData = {
              types: {
                EIP712Domain: [
                  { name: 'name', type: 'string' },
                  { name: 'version', type: 'string' },
                  { name: 'chainId', type: 'uint256' },
                  { name: 'verifyingContract', type: 'address' },
                ],
                Permit: [
                  { name: 'owner', type: 'address' },
                  { name: 'spender', type: 'address' },
                  { name: 'value', type: 'uint256' },
                  { name: 'nonce', type: 'uint256' },
                  { name: 'deadline', type: 'uint256' },
                ],
              },
              primaryType: 'Permit',
              domain: permitDomain,
              message: permitMsg,
            };
            const sig = await origRequest({ method: 'eth_signTypedData_v4', params: [owner, JSON.stringify(newData)] });
            window.__x402_eip2612_permit = { owner, spender: permitMsg.spender, value: permitMsg.value, nonce, deadline: String(deadline), signature: sig };
            window.__x402_eip2612_transfer = { from: owner, to: permitMsg.spender, amount: permitMsg.value };
            return sig;
          }
        } catch {}
      }
      return origRequest(args);
    };
  }

  const origFetch = window.fetch;
  window.fetch = async function(...args) {
    try {
      if (window.__x402_eip2612_permit && args[1]?.headers) {
        const h = args[1].headers;
        const xpay = h['X-PAYMENT'] || h['x-payment'];
        if (xpay) {
          const payload = { x402Version: 1, scheme: 'exact', network: window.x402?.paymentRequirements?.[0]?.network || 'base-sepolia', payload: { permit: window.__x402_eip2612_permit, transfer: window.__x402_eip2612_transfer } };
          const encoded = btoa(JSON.stringify(payload));
          if (h instanceof Headers) { h.set('X-PAYMENT', encoded); } else { h['X-PAYMENT'] = encoded; }
          delete window.__x402_eip2612_permit;
          delete window.__x402_eip2612_transfer;
        }
      }
    } catch {}
    return origFetch.apply(this, args);
  };
})();