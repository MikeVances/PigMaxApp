// feedback.js — инжектирует кнопку и модальное окно обратной связи на любую страницу

(function () {
  document.addEventListener('DOMContentLoaded', function () {

    // ── Кнопка ───────────────────────────────────────────────────────────────
    const btn = document.createElement('button');
    btn.className     = 'btn btn-secondary';
    btn.id            = 'feedbackTrigger';
    btn.dataset.i18n  = 'feedback_btn';
    btn.textContent   = '💬';
    btn.style.cssText =
      'position:fixed;bottom:12px;right:8px;z-index:9998;font-size:11px;padding:4px 9px;';
    document.body.appendChild(btn);

    // ── Модальное окно ────────────────────────────────────────────────────────
    const modal = document.createElement('div');
    modal.id        = 'feedbackModal';
    modal.className = 'modal hidden';
    modal.innerHTML = `
      <div class="modal-card" style="width:min(480px,92vw);">
        <h3 style="margin:0 0 10px;" data-i18n="feedback_title">Send Feedback</h3>
        <textarea id="feedbackText"
          rows="5"
          style="width:100%;box-sizing:border-box;resize:vertical;
                 background:var(--input-bg,#1e1e1e);color:inherit;
                 border:1px solid var(--border);border-radius:6px;
                 padding:8px;font-size:14px;font-family:inherit;"
          data-i18n-placeholder="feedback_placeholder"></textarea>
        <div id="feedbackStatus" style="min-height:18px;font-size:12px;margin-top:6px;"></div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:10px;">
          <button id="feedbackCancel" class="btn btn-secondary" data-i18n="feedback_cancel">Cancel</button>
          <button id="feedbackSend"   class="btn"               data-i18n="feedback_send">Send</button>
        </div>
      </div>`;
    document.body.appendChild(modal);

    // ── Применяем переводы если i18n уже загружен ─────────────────────────────
    if (window.I18N) window.I18N.applyDOM();

    const textEl   = modal.querySelector('#feedbackText');
    const statusEl = modal.querySelector('#feedbackStatus');
    const sendBtn  = modal.querySelector('#feedbackSend');
    const cancelBtn = modal.querySelector('#feedbackCancel');

    function openModal() {
      textEl.value    = '';
      statusEl.textContent = '';
      statusEl.style.color = '';
      modal.classList.remove('hidden');
      textEl.focus();
    }

    function closeModal() {
      modal.classList.add('hidden');
    }

    btn.addEventListener('click', openModal);
    cancelBtn.addEventListener('click', closeModal);
    modal.addEventListener('click', function (e) {
      if (e.target === modal) closeModal();
    });

    sendBtn.addEventListener('click', async function () {
      const text = textEl.value.trim();
      if (!text) return;

      sendBtn.disabled = true;
      statusEl.textContent = '…';
      statusEl.style.color = '';

      try {
        const resp = await fetch('./feedback', {
          method:  'POST',
          headers: { 'Content-Type': 'application/json' },
          body:    JSON.stringify({
            message: text,
            page:    document.title || location.pathname,
          }),
        });

        if (resp.ok) {
          statusEl.textContent = window.t ? window.t('feedback_sent') : 'Sent!';
          statusEl.style.color = '#4caf50';
          setTimeout(closeModal, 1500);
        } else {
          statusEl.textContent = window.t ? window.t('feedback_error') : 'Error. Try again.';
          statusEl.style.color = '#ff7a7a';
        }
      } catch (_) {
        statusEl.textContent = window.t ? window.t('feedback_error') : 'Error. Try again.';
        statusEl.style.color = '#ff7a7a';
      } finally {
        sendBtn.disabled = false;
      }
    });

  });
})();
