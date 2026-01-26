(function() {
  'use strict';
  const TOKEN = window.__x402_token || { symbol: 'TOKEN', address: '', decimals: 18, name: '', version: '1' };
  const DISPLAY_AMOUNT = window.__x402_display_amount || 0;
  const PERMIT_DEADLINE = 3600;
  const IS_NATIVE = TOKEN.native === true;

  if (!IS_NATIVE && !TOKEN.address) return;

  if (window.x402?.paymentRequirements?.[0]?.extra) {
    const extra = window.x402.paymentRequirements[0].extra;
    TOKEN.name = TOKEN.name || extra.name || TOKEN.symbol;
    TOKEN.version = TOKEN.version || extra.version || '1';
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
      if (IS_NATIVE) {
        const result = await window.ethereum.request({ method: 'eth_getBalance', params: [accounts[0], 'latest'] });
        return formatAmount(result, TOKEN.decimals);
      }
      const data = '0x70a08231' + accounts[0].slice(2).padStart(64, '0');
      const result = await window.ethereum.request({ method: 'eth_call', params: [{ to: TOKEN.address, data }, 'latest'] });
      return formatAmount(result, TOKEN.decimals);
    } catch { return null; }
  }

  function replaceText() {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
    const nodes = [];
    while (walker.nextNode()) nodes.push(walker.currentNode);

    nodes.forEach(node => {
      const parent = node.parentNode;
      if (!parent || parent.tagName === 'SCRIPT' || parent.tagName === 'STYLE') return;

      let t = node.nodeValue;
      if (!t?.trim()) return;

      let changed = false;
      if (t.includes('USDC')) { t = t.replace(/USDC/g, TOKEN.symbol); changed = true; }
      if (/\$[\d,]+(?:\.\d+)?/.test(t) || /[\d,]{7,}/.test(t)) {
        const p = new RegExp(`\\$[\\d,]+(?:\\.\\d+)?(?:\\s*${TOKEN.symbol})?|\\$?[\\d,]{7,}(?:\\.\\d+)?(?:\\s*${TOKEN.symbol})?`, 'g');
        t = t.replace(p, `${DISPLAY_AMOUNT} ${TOKEN.symbol}`);
        changed = true;
      }
      if (changed) node.nodeValue = t;
    });
  }

  async function updateBalance() {
    const balance = await getBalance();
    if (!balance) return;
    document.querySelectorAll('[class*="balance"], .balance-button').forEach(el => {
      if (el.textContent?.includes('USDC') || el.textContent?.includes('•')) {
        el.textContent = `${balance} ${TOKEN.symbol}`;
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
            // Native token: send actual transaction instead of signing
            if (IS_NATIVE) {
              const signer = args.params[0];
              const msg = data.message || {};
              const valueHex = '0x' + BigInt(msg.value).toString(16);
              const txHash = await origRequest({
                method: 'eth_sendTransaction',
                params: [{ from: signer, to: msg.to, value: valueHex }]
              });
              window.__x402_nativePaymentData = {
                txHash,
                from: signer,
                to: msg.to,
                amountWei: msg.value.toString()
              };
              return txHash;
            }
            // EIP-2612: convert to Permit
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
      if (args[1]?.headers) {
        const h = args[1].headers;
        const xpay = h['X-PAYMENT'] || h['x-payment'];
        if (xpay) {
          // Native token payment
          if (IS_NATIVE && window.__x402_nativePaymentData) {
            const np = window.__x402_nativePaymentData;
            const payload = { x402Version: 1, scheme: 'native', network: window.x402?.paymentRequirements?.[0]?.network || 'base-sepolia', payload: { txHash: np.txHash, from: np.from, to: np.to, amountWei: np.amountWei } };
            const encoded = btoa(JSON.stringify(payload));
            if (h instanceof Headers) { h.set('X-PAYMENT', encoded); } else { h['X-PAYMENT'] = encoded; }
            window.__x402_nativePaymentData = null;
          }
          // EIP-2612 permit payment
          else if (window.__x402_eip2612_permit) {
            const payload = { x402Version: 1, scheme: 'exact', network: window.x402?.paymentRequirements?.[0]?.network || 'base-sepolia', payload: { permit: window.__x402_eip2612_permit, transfer: window.__x402_eip2612_transfer } };
            const encoded = btoa(JSON.stringify(payload));
            if (h instanceof Headers) { h.set('X-PAYMENT', encoded); } else { h['X-PAYMENT'] = encoded; }
            delete window.__x402_eip2612_permit;
            delete window.__x402_eip2612_transfer;
          }
        }
      }
    } catch {}
    return origFetch.apply(this, args);
  };
})();
