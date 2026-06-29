/**
 * main.js — doubtundo.app client-side interactions
 * Handles: upvote AJAX, flash auto-dismiss, filter toggles
 */

document.addEventListener('DOMContentLoaded', function () {

  // Collapse blockquotes helper for quote replies
  function collapseBlockquotes(container) {
    if (!container) return;
    container.querySelectorAll('.reply-content blockquote').forEach(function(bq) {
      if (bq.closest('.quote-collapse-container')) return;

      const text = bq.textContent || '';
      const match = text.match(/@([a-zA-Z0-9_]+)/);
      const nickname = match ? '@' + match[1] : 'another reply';
      
      const wrapper = document.createElement('div');
      wrapper.className = 'quote-collapse-container';
      
      const summary = document.createElement('div');
      summary.className = 'quote-collapse-header';
      summary.innerHTML = `<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" class="chevron" style="margin-right: 4px; transition: transform 0.2s;"><polyline points="6 9 12 15 18 9"/></svg><span>Replying to ${nickname}</span>`;
      
      const content = document.createElement('div');
      content.className = 'quote-collapse-content';
      content.appendChild(bq.cloneNode(true));
      
      wrapper.appendChild(summary);
      wrapper.appendChild(content);
      
      bq.parentNode.replaceChild(wrapper, bq);
      
      summary.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
        wrapper.classList.toggle('open');
      });
    });
  }

  // Process existing page blockquotes
  window.collapseBlockquotes = collapseBlockquotes;
  collapseBlockquotes(document);

  // ============================================================
  // 1. UPVOTE BUTTONS (AJAX)
  // ============================================================
  document.querySelectorAll('.btn-upvote[data-id]').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();

      const targetId = btn.dataset.id;
      const targetType = btn.dataset.type; // 'doubt' | 'reply'
      let countEl = btn.querySelector('.upvote-count');
      if (!countEl) {
        countEl = btn.nextElementSibling;
      }

      // Optimistic UI
      const wasVoted = btn.classList.contains('voted');
      btn.classList.toggle('voted');
      btn.setAttribute('aria-pressed', !wasVoted);
      if (countEl) {
        const current = parseInt(countEl.textContent, 10) || 0;
        countEl.textContent = wasVoted ? Math.max(0, current - 1) : current + 1;
      }

      // AJAX
      fetch('/upvote', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ id: targetId, type: targetType }),
      })
        .then(function (res) {
          if (!res.ok) throw new Error('Server error');
          return res.json();
        })
        .then(function (data) {
          // Sync with server truth
          if (data.voted !== undefined) {
            btn.classList.toggle('voted', data.voted);
            btn.setAttribute('aria-pressed', data.voted);
          }
          if (countEl && data.count !== undefined) {
            countEl.textContent = data.count;
          }
        })
        .catch(function () {
          // Rollback optimistic UI
          btn.classList.toggle('voted', wasVoted);
          btn.setAttribute('aria-pressed', wasVoted);
          if (countEl) {
            const current = parseInt(countEl.textContent, 10) || 0;
            countEl.textContent = wasVoted ? current + 1 : Math.max(0, current - 1);
          }
          showToast('Could not update upvote. Please try again.', 'error');
        });
    });
  });

  // ============================================================
  // 2. FLASH MESSAGE AUTO-DISMISS (4 seconds)
  // ============================================================
  var flashes = document.querySelectorAll('.flash');
  flashes.forEach(function (flash) {
    setTimeout(function () {
      flash.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
      flash.style.opacity = '0';
      flash.style.transform = 'translateX(20px)';
      setTimeout(function () { flash.remove(); }, 500);
    }, 4000);
  });

  // ============================================================
  // 3. FILTER TOGGLE VISUAL STATE (checkboxes)
  // ============================================================
  document.querySelectorAll('.filter-toggle input[type="checkbox"]').forEach(function (cb) {
    cb.addEventListener('change', function () {
      cb.closest('.filter-toggle').classList.toggle('active', cb.checked);
    });
  });

  // ============================================================
  // 4. SORT TAB — active class on click (before page reload)
  // ============================================================
  document.querySelectorAll('.sort-tab').forEach(function (tab) {
    tab.addEventListener('click', function () {
      document.querySelectorAll('.sort-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
    });
  });

  // ============================================================
  // 5. FORM SUBMIT LOADING STATE
  // ============================================================
  document.querySelectorAll('form[id]').forEach(function (form) {
    form.addEventListener('submit', function () {
      var btn = form.querySelector('button[type="submit"]');
      if (btn) {
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Posting…';
      }
    });
  });

  // ============================================================
  // 6. DOUBT CARD CLICK (navigate to detail, not when clicking action buttons)
  // ============================================================
  // Cards are already <a> tags, so this is handled natively

  // ============================================================
  // 7. AVATAR HOVER EFFECT (landing page)
  // ============================================================
  document.querySelectorAll('.avatar-circle').forEach(function (av) {
    av.addEventListener('mouseenter', function () {
      av.style.zIndex = '10';
    });
    av.addEventListener('mouseleave', function () {
      av.style.zIndex = '';
    });
  });

  // ============================================================
  // 8. REPLY TEXTAREA AUTO-RESIZE
  // ============================================================
  var replyTA = document.getElementById('reply-content');
  if (replyTA) {
    replyTA.addEventListener('input', function () {
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, 300) + 'px';
    });
  }

  // ============================================================
  // 9. INLINE DISCUSSION DROPDOWN & AJAX REPLY
  // ============================================================
  document.querySelectorAll('.btn-toggle-replies').forEach(function (btn) {
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();

      const doubtId = btn.dataset.doubtId;
      const dropdown = document.getElementById('replies-dropdown-' + doubtId);
      if (!dropdown) return;

      // Toggle display
      if (dropdown.style.display === 'none') {
        dropdown.style.display = 'block';
        loadInlineReplies(doubtId, dropdown, btn);
      } else {
        dropdown.style.display = 'none';
      }
    });
  });

  function loadInlineReplies(doubtId, dropdown, btn) {
    dropdown.innerHTML = '<div class="spinner-wrapper" style="text-align: center; padding: 20px 0;"><span class="spinner"></span> Loading discussion...</div>';
    
    fetch('/doubt/' + doubtId + '/replies-inline')
      .then(function (res) {
        if (!res.ok) throw new Error('Failed to load discussion');
        return res.text();
      })
      .then(function (html) {
        dropdown.innerHTML = html;
        bindInlineReplyForm(doubtId, dropdown, btn);
        bindInlineQuoteButtons(dropdown);
        if (window.collapseBlockquotes) {
          window.collapseBlockquotes(dropdown);
        }
      })
      .catch(function (err) {
        dropdown.innerHTML = '<p style="text-align: center; color: var(--red); padding: 20px 0; font-size:13px;">⚠️ Error loading replies. Click Replies to retry.</p>';
      });
  }

  // Bind inline quote-reply buttons (AJAX loaded, so inline <script> doesn't run)
  function bindInlineQuoteButtons(container) {
    var textarea    = container.querySelector('.inline-reply-form textarea[name="content"]');
    var quotePrefix = '';

    // Ensure a dismiss banner exists above the textarea
    var form = container.querySelector('.inline-reply-form');
    var banner = container.querySelector('.inline-quote-banner');
    if (!banner && form && textarea) {
      banner = document.createElement('div');
      banner.className = 'inline-quote-banner';
      banner.style.cssText = 'display:none; align-items:center; justify-content:space-between; background:var(--gray-50); border:1px solid var(--gray-200); border-radius:6px; padding:5px 10px; margin-bottom:6px; font-size:11px; color:var(--gray-500);';
      banner.innerHTML = '<span class="iqb-label" style="font-weight:600;">↩ Replying to @…</span>' +
                         '<button type="button" class="iqb-clear" aria-label="Cancel quote reply" style="background:none;border:none;cursor:pointer;color:var(--gray-400);font-size:13px;font-weight:700;padding:0 2px;line-height:1;">✕</button>';
      textarea.parentNode.insertBefore(banner, textarea);
    }

    container.querySelectorAll('.btn-inline-reply-quote').forEach(function(btn) {
      btn.addEventListener('click', function(e) {
        e.preventDefault();
        e.stopPropagation();
        var nickname = btn.getAttribute('data-nickname');
        var content  = btn.getAttribute('data-content');
        if (!textarea) return;

        // Strip existing prefix first
        if (quotePrefix && textarea.value.startsWith(quotePrefix)) {
          textarea.value = textarea.value.slice(quotePrefix.length);
        }

        quotePrefix = '> @' + nickname + ': "' + content + '"\n\n';
        textarea.value = quotePrefix + textarea.value;
        textarea.focus();
        textarea.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

        // Show banner
        if (banner) {
          banner.querySelector('.iqb-label').textContent = '↩ Replying to @' + nickname;
          banner.style.display = 'flex';
        }
      });
    });

    // Wire up the ✕ clear button
    if (banner) {
      var clearBtn = banner.querySelector('.iqb-clear');
      if (clearBtn) {
        clearBtn.addEventListener('click', function() {
          if (textarea && quotePrefix && textarea.value.startsWith(quotePrefix)) {
            textarea.value = textarea.value.slice(quotePrefix.length);
          }
          quotePrefix = '';
          banner.style.display = 'none';
          if (textarea) textarea.focus();
        });
      }
    }

    // Auto-hide banner if user manually edits away the quote
    if (textarea) {
      textarea.addEventListener('input', function() {
        if (quotePrefix && !textarea.value.startsWith(quotePrefix)) {
          quotePrefix = '';
          if (banner) banner.style.display = 'none';
        }
      });
    }
  }

  function bindInlineReplyForm(doubtId, dropdown, btn) {
    const form = dropdown.querySelector('.inline-reply-form');
    if (!form) return;

    form.addEventListener('submit', function (e) {
      e.preventDefault();
      e.stopPropagation();

      const submitBtn = form.querySelector('button[type="submit"]');
      const textarea = form.querySelector('textarea');
      const content = textarea.value.strip ? textarea.value.strip() : textarea.value.trim();

      if (!content || content.length < 3) {
        showToast('Reply must be at least 3 characters.', 'error');
        return;
      }

      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = 'Posting...';
      }

      const formData = new FormData(form);

      fetch('/doubt/' + doubtId + '/reply-ajax', {
        method: 'POST',
        body: formData,
        credentials: 'same-origin'
      })
        .then(function (res) {
          if (!res.ok) return res.json().then(function (data) { throw new Error(data.error || 'Server error') });
          return res.json();
        })
        .then(function (data) {
          showToast('Reply posted! 💬', 'success');
          // Reload replies list
          loadInlineReplies(doubtId, dropdown, btn);
          
          // Increment reply count on parent button
          const labelSpan = btn.querySelector('span');
          if (labelSpan) {
            const match = labelSpan.textContent.match(/\d+/);
            if (match) {
              const newCount = parseInt(match[0], 10) + 1;
              labelSpan.textContent = newCount + ' Replies';
            }
          }
        })
        .catch(function (err) {
          showToast(err.message || 'Error posting reply.', 'error');
          if (submitBtn) {
            submitBtn.disabled = false;
            submitBtn.textContent = 'Comment';
          }
        });
    });
  }

  // ============================================================
  // UTILITY: showToast
  // ============================================================
  function showToast(message, type) {
    type = type || 'info';
    var container = document.getElementById('flash-messages');
    if (!container) {
      container = document.createElement('div');
      container.className = 'flash-messages';
      container.id = 'flash-messages';
      document.body.appendChild(container);
    }
    var icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
    var toast = document.createElement('div');
    toast.className = 'flash flash--' + type;
    toast.textContent = (icons[type] || '') + ' ' + message;
    toast.onclick = function () { toast.remove(); };
    container.appendChild(toast);
    setTimeout(function () {
      toast.style.transition = 'opacity 0.5s ease';
      toast.style.opacity = '0';
      setTimeout(function () { toast.remove(); }, 500);
    }, 4000);
  }

  // Expose globally for other scripts
  window.showToast = showToast;

});
