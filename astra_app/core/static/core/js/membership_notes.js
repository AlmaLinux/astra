/**
 * Membership Committee Notes shared widget behavior.
 *
 * Handles AJAX posting, collapse state persistence, summary/detail loading,
 * and grouped note rendering for both queue rows and standalone detail pages.
 */
(function () {
  'use strict';
  if (window.AstraMembershipNotes && window.AstraMembershipNotes._initialized) return;

  var ns = window.AstraMembershipNotes = window.AstraMembershipNotes || {};
  ns._initialized = true;

  ns._storageKey = function (requestPk) {
    return 'membership_notes_open_' + requestPk;
  };

  ns.escapeHtml = function (value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  };

  ns.escapeHtmlWithLineBreaks = function (value) {
    return ns.escapeHtml(value).replace(/\r\n|\r|\n/g, '<br>');
  };

  ns.markSummaryUnavailable = function (container, requestPk) {
    if (!container) return;

    var countElement = container.querySelector('[data-membership-notes-count="' + requestPk + '"]');
    if (countElement) {
      var existingText = String(countElement.textContent || '').trim();
      if (!existingText || existingText === '...') {
        countElement.textContent = '!';
      }
      countElement.className = 'badge badge-warning';
      countElement.setAttribute('title', 'Note summary unavailable');
    }
  };

  ns.buildEmailDetailModalId = function (requestPk, contactedEmail) {
    return 'membership-email-modal-' + ns.escapeHtml(requestPk) + '-' + ns.escapeHtml(contactedEmail.email_id || '');
  };

  ns.buildEmailDetailsModalHtml = function (requestPk, contactedEmail) {
    if (!contactedEmail) return '';

    var modalId = ns.buildEmailDetailModalId(requestPk, contactedEmail);
    var html = '';
    html += '<div class="modal fade" id="' + modalId + '" tabindex="-1" role="dialog" aria-hidden="true" aria-labelledby="' + modalId + '-title">';
    html += '<div class="modal-dialog modal-lg" role="document"><div class="modal-content">';
    html += '<div class="modal-header"><h5 class="modal-title" id="' + modalId + '-title">Email details</h5>';
    html += '<button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close email details"><span aria-hidden="true">&times;</span></button></div>';
    html += '<div class="modal-body"><dl class="row mb-0">';
    html += '<dt class="col-sm-3">To</dt><dd class="col-sm-9">' + ns.escapeHtml((contactedEmail.to || []).join(', ')) + '</dd>';
    html += '<dt class="col-sm-3">Subject</dt><dd class="col-sm-9">' + ns.escapeHtml(contactedEmail.subject || '') + '</dd>';
    if (contactedEmail.from_email) {
      html += '<dt class="col-sm-3">From</dt><dd class="col-sm-9">' + ns.escapeHtml(contactedEmail.from_email) + '</dd>';
    }
    if (contactedEmail.cc && contactedEmail.cc.length) {
      html += '<dt class="col-sm-3">CC</dt><dd class="col-sm-9">' + ns.escapeHtml(contactedEmail.cc.join(', ')) + '</dd>';
    }
    if (contactedEmail.bcc && contactedEmail.bcc.length) {
      html += '<dt class="col-sm-3">BCC</dt><dd class="col-sm-9">' + ns.escapeHtml(contactedEmail.bcc.join(', ')) + '</dd>';
    }
    if (contactedEmail.reply_to) {
      html += '<dt class="col-sm-3">Reply-To</dt><dd class="col-sm-9">' + ns.escapeHtml(contactedEmail.reply_to) + '</dd>';
    }
    html += '<dt class="col-sm-3">Recipient delivery summary</dt><dd class="col-sm-9">' + ns.escapeHtml(contactedEmail.recipient_delivery_summary || '');
    if (contactedEmail.recipient_delivery_summary_note) {
      html += '<div class="text-muted small">' + ns.escapeHtml(contactedEmail.recipient_delivery_summary_note) + '</div>';
    }
    html += '</dd></dl>';
    if (contactedEmail.headers && contactedEmail.headers.length) {
      html += '<div class="mt-3"><div class="text-muted small mb-1">Other headers</div><ul class="small mb-0">';
      contactedEmail.headers.forEach(function (header) {
        html += '<li><strong>' + ns.escapeHtml(header[0] || '') + ':</strong> ' + ns.escapeHtml(header[1] || '') + '</li>';
      });
      html += '</ul></div>';
    }
    html += '<hr><h6 class="text-muted">HTML</h6>';
    if (contactedEmail.html) {
      html += '<iframe srcdoc="' + ns.escapeHtml(contactedEmail.html) + '" sandbox title="Email HTML preview" style="width: 100%; height: 320px; border: 1px solid #dee2e6; border-radius: .25rem;"></iframe>';
    } else {
      html += '<div class="border rounded p-2 bg-light text-muted small">No HTML content.</div>';
    }
    html += '<h6 class="text-muted mt-3">Plain text</h6><pre class="border rounded p-2 bg-light" style="max-height: 260px; overflow: auto; white-space: pre-wrap;">' + ns.escapeHtml(contactedEmail.text || '') + '</pre>';
    html += '<hr><h6 class="text-muted">Delivery logs</h6>';
    if (contactedEmail.logs && contactedEmail.logs.length) {
      html += '<table class="table table-sm"><thead><tr><th style="width: 30%;">Time</th><th style="width: 20%;">Status</th><th>Message</th></tr></thead><tbody>';
      contactedEmail.logs.forEach(function (log) {
        html += '<tr><td>' + ns.escapeHtml(log.date_display || '') + '</td><td>' + ns.escapeHtml(log.status || '') + '</td><td>' + ns.escapeHtml(log.message || '');
        if (log.exception_type) {
          html += '<div class="text-muted small">' + ns.escapeHtml(log.exception_type) + '</div>';
        }
        html += '</td></tr>';
      });
      html += '</tbody></table>';
    } else {
      html += '<div class="text-muted small">No delivery logs recorded.</div>';
    }
    html += '</div><div class="modal-footer"><button type="button" class="btn btn-secondary" data-dismiss="modal" title="Close email details">Close</button></div>';
    html += '</div></div></div>';
    return html;
  };

  ns.buildNotesGroupsHtml = function (groups, requestPk) {
    if (!groups || !groups.length) {
      return {
        groupsHtml: '<div class="text-muted small">No notes yet.</div>',
        modalsHtml: ''
      };
    }

    var html = '';
    var modalsHtml = '';
    groups.forEach(function (group, index) {
      var membershipRequestLinkHtml = '';
      if (group.membership_request_id && group.membership_request_url) {
        membershipRequestLinkHtml = '<a href="' + ns.escapeHtml(group.membership_request_url) + '" class="text-muted ' + (group.is_self ? 'ml-1' : 'mr-1') + '">(req. #' + ns.escapeHtml(group.membership_request_id) + ')</a>';
      }

      html += '<div class="direct-chat-msg' + (index + 1 < groups.length ? ' mb-3' : '') + (group.is_self ? ' right' : '') + '" data-membership-notes-group-username="' + ns.escapeHtml(group.username || '') + '">';
      html += '<div class="direct-chat-infos clearfix">';
      if (group.is_self) {
        html += '<span class="direct-chat-name float-right">' + ns.escapeHtml(group.display_username || group.username || '') + '</span>';
        html += '<span class="direct-chat-timestamp float-left">' + ns.escapeHtml(group.timestamp_display || '') + membershipRequestLinkHtml + '</span>';
      } else {
        html += '<span class="direct-chat-name float-left">' + ns.escapeHtml(group.display_username || group.username || '') + '</span>';
        html += '<span class="direct-chat-timestamp float-right">' + membershipRequestLinkHtml + ns.escapeHtml(group.timestamp_display || '') + '</span>';
      }
      html += '</div>';

      if (group.avatar_url) {
        html += '<img class="direct-chat-img' + (group.avatar_kind === 'user' ? ' img-circle' : '') + '" src="' + ns.escapeHtml(group.avatar_url) + '" alt="' + (group.avatar_kind === 'user' ? 'User Avatar' : 'Astra Custodia') + '"' + (group.avatar_kind === 'user' ? ' style="object-fit:cover;"' : ' style="object-fit: cover; background: #fff;"') + '>';
      } else {
        html += '<img class="direct-chat-img" src="/static/core/images/almalinux-logo.svg" alt="user image">';
      }

      html += '<div class="membership-notes-bubbles">';
      (group.entries || []).forEach(function (entry) {
        if (entry.kind === 'action') {
          html += '<div class="direct-chat-text bg-light membership-notes-bubble" style="' + ns.escapeHtml(entry.bubble_style || '') + ' border: 1px dashed rgba(0,0,0,0.15);">';
          html += '<i class="fas ' + ns.escapeHtml(entry.icon || 'fa-bolt') + ' mr-1"></i> ' + ns.escapeHtml(entry.label || '');
          if (entry.contacted_email) {
            var modalId = ns.buildEmailDetailModalId(requestPk, entry.contacted_email);
            html += '<button type="button" class="btn btn-link btn-sm p-0 ml-2" data-toggle="modal" data-target="#' + modalId + '" aria-label="View email">View email</button>';
            modalsHtml += ns.buildEmailDetailsModalHtml(requestPk, entry.contacted_email);
          }
          if (entry.request_resubmitted_diff_rows && entry.request_resubmitted_diff_rows.length) {
            html += '<div class="mt-2" data-request-resubmitted-note-id="' + ns.escapeHtml(entry.note_id || '') + '">';
            entry.request_resubmitted_diff_rows.forEach(function (diffRow) {
              html += '<details class="mt-1" data-request-resubmitted-question="' + ns.escapeHtml(diffRow.question || '') + '">';
              html += '<summary>' + ns.escapeHtml(diffRow.question || '') + '</summary>';
              html += '<div class="small text-muted mt-2">Previous response</div>';
              html += '<div data-request-resubmitted-old>' + ns.escapeHtmlWithLineBreaks(diffRow.old_value || '') + '</div>';
              html += '<div class="small text-muted mt-2">Updated response</div>';
              html += '<div data-request-resubmitted-new>' + ns.escapeHtmlWithLineBreaks(diffRow.new_value || '') + '</div>';
              html += '</details>';
            });
            html += '</div>';
          }
          html += '</div>';
          return;
        }

        if (!entry.is_self && entry.bubble_style) {
          var bubbleStyle = ns.escapeHtml(entry.bubble_style || '');
          if (entry.is_custos) {
            bubbleStyle += ' border: 1px dashed rgba(0,0,0,0.15);';
          }
          html += '<div class="direct-chat-text membership-notes-bubble" style="' + bubbleStyle + '">' + String(entry.rendered_html || '') + '</div>';
          return;
        }

        html += '<div class="direct-chat-text' + (entry.is_self ? ' membership-notes-self-bubble' : '') + '">' + String(entry.rendered_html || '') + '</div>';
      });
      html += '</div></div>';
    });

    return {
      groupsHtml: html,
      modalsHtml: modalsHtml
    };
  };

  ns.init = function (requestPk) {
    var container = document.getElementById('membership-notes-container-' + requestPk);
    if (!container || container.dataset.membershipNotesInitialized) return;
    container.dataset.membershipNotesInitialized = '1';

    var card = document.getElementById('membership-notes-card-' + requestPk);
    var form = document.getElementById('membership-notes-form-' + requestPk);
    var message = document.getElementById('membership-notes-message-' + requestPk);
    var collapseBtn = container.querySelector('[data-membership-notes-collapse="' + requestPk + '"]');
    var header = container.querySelector('[data-membership-notes-header="' + requestPk + '"]');

    function getMessagesEl() {
      return container.querySelector('[data-membership-notes-messages="' + requestPk + '"]');
    }

    function getModalsEl() {
      return container.querySelector('[data-membership-notes-modals="' + requestPk + '"]');
    }

    function hasFallbackContent() {
      if (String(container.getAttribute('data-membership-notes-has-fallback-content') || '').trim() === '1') {
        return true;
      }

      var messagesElement = getMessagesEl();
      if (!messagesElement) return false;
      var existingHtml = String(messagesElement.innerHTML || '').trim();
      if (!existingHtml) return false;
      return existingHtml.indexOf('Loading notes...') === -1;
    }

    function showReadError(errorMessage) {
      var box = document.getElementById('membership-notes-error-' + requestPk);
      var text = document.getElementById('membership-notes-error-text-' + requestPk);
      if (text) {
        text.textContent = errorMessage;
      }
      if (box) {
        box.classList.remove('d-none');
      }
    }

    function clearReadError() {
      var box = document.getElementById('membership-notes-error-' + requestPk);
      var text = document.getElementById('membership-notes-error-text-' + requestPk);
      if (text) {
        text.textContent = '';
      }
      if (box) {
        box.classList.add('d-none');
      }
    }

    function summaryUrl() {
      return String(container.getAttribute('data-membership-notes-summary-url') || '').trim();
    }

    function detailUrl() {
      return String(container.getAttribute('data-membership-notes-detail-url') || '').trim();
    }

    function detailsLoaded() {
      return String(container.getAttribute('data-membership-notes-details-loaded') || '').trim() === 'true';
    }

    function setDetailsLoaded(isLoaded) {
      container.setAttribute('data-membership-notes-details-loaded', isLoaded ? 'true' : 'false');
    }

    function setOpenState(isOpen) {
      try {
        window.localStorage.setItem(ns._storageKey(requestPk), isOpen ? '1' : '0');
      } catch (e) {
        // Ignore storage errors.
      }
    }

    function readStoredOpenState() {
      try {
        return window.localStorage.getItem(ns._storageKey(requestPk));
      } catch (e) {
        return null;
      }
    }

    function getDesiredOpenState() {
      var stored = readStoredOpenState();
      if (stored === '1') return true;
      if (stored === '0') return false;
      return container && container.dataset && container.dataset.membershipNotesDefaultOpen === '1';
    }

    function applyNoteSummary(summary) {
      if (!summary) return;

      var currentVote = String(summary.current_user_vote || '');
      var countElement = container.querySelector('[data-membership-notes-count="' + requestPk + '"]');
      var approvalsElement = container.querySelector('[data-membership-notes-approvals="' + requestPk + '"]');
      var disapprovalsElement = container.querySelector('[data-membership-notes-disapprovals="' + requestPk + '"]');

      if (countElement) {
        countElement.className = 'badge badge-primary';
        countElement.textContent = String(summary.note_count || 0);
        countElement.setAttribute('title', String(summary.note_count || 0) + ' Messages');
      }
      if (approvalsElement) {
        approvalsElement.className = 'badge ' + (currentVote === 'approve' ? 'badge-warning' : 'badge-success');
        approvalsElement.innerHTML = '<i class="fas fa-thumbs-up"></i> ' + ns.escapeHtml(summary.approvals || 0);
      }
      if (disapprovalsElement) {
        disapprovalsElement.className = 'badge ' + (currentVote === 'disapprove' ? 'badge-warning' : 'badge-danger');
        disapprovalsElement.innerHTML = '<i class="fas fa-thumbs-down"></i> ' + ns.escapeHtml(summary.disapprovals || 0);
      }
    }

    function loadNoteSummary() {
      if (!summaryUrl()) {
        return Promise.resolve();
      }

      return window.fetch(summaryUrl(), { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
        .then(function (response) {
          return response.json().then(function (payload) {
            return { ok: response.ok, payload: payload };
          });
        })
        .then(function (result) {
          if (!result.ok) {
            throw new Error((result.payload && result.payload.error) || 'Failed to load note summary.');
          }
          applyNoteSummary(result.payload);
        })
        .catch(function () {
          ns.markSummaryUnavailable(container, requestPk);
        });
    }

    var loadingDetail = false;

    function applyNoteDetails(details) {
      var messagesElement = getMessagesEl();
      var modalsElement = getModalsEl();
      var notesGroups = ns.buildNotesGroupsHtml(details && details.groups ? details.groups : [], requestPk);

      clearReadError();
      if (messagesElement) {
        messagesElement.innerHTML = notesGroups.groupsHtml;
      }
      if (modalsElement) {
        modalsElement.innerHTML = notesGroups.modalsHtml;
      }
      setDetailsLoaded(true);
    }

    function loadNoteDetails() {
      if (!detailUrl() || detailsLoaded() || loadingDetail) {
        return Promise.resolve();
      }

      var messagesElement = getMessagesEl();
      loadingDetail = true;
      setDetailsLoaded(false);
      if (messagesElement && !hasFallbackContent()) {
        messagesElement.innerHTML = '<div class="text-muted small">Loading notes...</div>';
      }

      return window.fetch(detailUrl(), { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
        .then(function (response) {
          return response.json().then(function (payload) {
            return { ok: response.ok, payload: payload };
          });
        })
        .then(function (result) {
          if (!result.ok) {
            throw new Error((result.payload && result.payload.error) || 'Failed to load notes.');
          }
          applyNoteDetails(result.payload);
        })
        .catch(function (error) {
          setDetailsLoaded(false);
          if (messagesElement && !hasFallbackContent()) {
            messagesElement.innerHTML = '<div class="text-danger small">' + ns.escapeHtml(error.message || 'Failed to load notes.') + '</div>';
          }
          if (hasFallbackContent()) {
            showReadError('Could not refresh note history. Showing initial note history.');
            return;
          }
          showReadError(error && error.message ? String(error.message) : 'Failed to load notes.');
        })
        .finally(function () {
          loadingDetail = false;
        });
    }

    function ensureDesiredOpenState() {
      if (!card || !collapseBtn) return;

      var desiredOpen = getDesiredOpenState();
      var isCollapsed = card.classList.contains('collapsed-card');
      if (desiredOpen && isCollapsed) {
        collapseBtn.click();
      } else if (!desiredOpen && !isCollapsed) {
        collapseBtn.click();
      }
    }

    function scrollToBottom() {
      var msgs = getMessagesEl();
      if (!msgs) return;
      msgs.scrollTop = msgs.scrollHeight;
    }

    function scrollToBottomWhenReady(maxWaitMs) {
      if (!card) {
        scrollToBottom();
        return;
      }

      var start = Date.now();

      function tick() {
        var msgs = getMessagesEl();
        if (!msgs) return;

        var isOpen = !card.classList.contains('collapsed-card');
        var isVisible = msgs.clientHeight > 0;
        if (isOpen && isVisible) {
          msgs.scrollTop = msgs.scrollHeight;
          setTimeout(function () {
            var nextMessages = getMessagesEl();
            if (nextMessages) nextMessages.scrollTop = nextMessages.scrollHeight;
          }, 0);
          return;
        }

        if (Date.now() - start > maxWaitMs) {
          msgs.scrollTop = msgs.scrollHeight;
          return;
        }

        setTimeout(tick, 50);
      }

      tick();
    }

    function scrollToBottomSoon() {
      scrollToBottom();
      scrollToBottomWhenReady(2000);
    }

    function handleOpen() {
      setOpenState(true);
      loadNoteDetails().finally(function () {
        scrollToBottomSoon();
      });
    }

    function ensureOpenWidgetDetailsLoaded() {
      if (!card || card.classList.contains('collapsed-card')) {
        return false;
      }
      handleOpen();
      return true;
    }

    function scheduleOpenWidgetDetailsRecovery(remainingChecks) {
      if (ensureOpenWidgetDetailsLoaded() || remainingChecks <= 0 || detailsLoaded() || loadingDetail) {
        return;
      }
      setTimeout(function () {
        scheduleOpenWidgetDetailsRecovery(remainingChecks - 1);
      }, 50);
    }

    function ajaxSubmit(action) {
      if (!form) return;

      setOpenState(true);
      ensureDesiredOpenState();

      var errorBox = document.getElementById('membership-notes-error-' + requestPk);
      var errorText = document.getElementById('membership-notes-error-text-' + requestPk);
      if (errorBox) {
        errorBox.classList.add('d-none');
      }
      if (errorText) {
        errorText.textContent = '';
      }

      var actionInput = form.querySelector('input[name="note_action"]');
      if (actionInput) actionInput.value = action;

      var formData = new FormData(form);
      formData.set('note_action', action);

      var buttons = form.querySelectorAll('button');
      for (var i = 0; i < buttons.length; i += 1) buttons[i].disabled = true;

      fetch(form.action, {
        method: 'POST',
        body: formData,
        headers: {
          'X-Requested-With': 'XMLHttpRequest'
        },
        credentials: 'same-origin'
      })
        .then(function (resp) {
          return resp.json().then(function (data) {
            return { status: resp.status, data: data };
          });
        })
        .then(function (result) {
          if (!result || !result.data || !result.data.ok || !result.data.html) {
            throw new Error((result && result.data && result.data.error) || 'Failed');
          }

          var currentContainer = document.getElementById('membership-notes-container-' + requestPk);
          if (!currentContainer) return;

          var refreshSection = String(currentContainer.getAttribute('data-membership-notes-refresh-section') || '').trim();
          if (refreshSection && window.CustomEvent) {
            var refreshEvent = new window.CustomEvent('astra:membership-notes-posted', {
              bubbles: true,
              cancelable: true,
              detail: {
                requestPk: requestPk,
                section: refreshSection,
                message: result.data.message || ''
              }
            });
            currentContainer.dispatchEvent(refreshEvent);
            if (refreshEvent.defaultPrevented) {
              if (message) {
                message.value = '';
              }
              return;
            }
          }

          var tmp = document.createElement('div');
          tmp.innerHTML = result.data.html;
          var newContainer = tmp.querySelector('#membership-notes-container-' + requestPk);
          if (!newContainer) return;

          var newMessages = newContainer.querySelector('[data-membership-notes-messages="' + requestPk + '"]');
          var currentMessages = getMessagesEl();
          if (currentMessages && newMessages) {
            currentMessages.innerHTML = newMessages.innerHTML;
          }

          var newModals = newContainer.querySelector('[data-membership-notes-modals="' + requestPk + '"]');
          var currentModals = getModalsEl();
          if (currentModals && newModals) {
            currentModals.innerHTML = newModals.innerHTML;
          }

          var newCount = newContainer.querySelector('[data-membership-notes-count="' + requestPk + '"]');
          var curCounts = currentContainer.querySelectorAll('[data-membership-notes-count="' + requestPk + '"]');
          if (newCount && curCounts && curCounts.length) {
            for (var cIdx = 0; cIdx < curCounts.length; cIdx += 1) {
              curCounts[cIdx].textContent = newCount.textContent;
              curCounts[cIdx].setAttribute('title', newCount.getAttribute('title') || '');
            }
          }

          var newApprovals = newContainer.querySelector('[data-membership-notes-approvals="' + requestPk + '"]');
          var curApprovals = currentContainer.querySelectorAll('[data-membership-notes-approvals="' + requestPk + '"]');
          if (newApprovals && curApprovals && curApprovals.length) {
            for (var aIdx = 0; aIdx < curApprovals.length; aIdx += 1) {
              curApprovals[aIdx].innerHTML = newApprovals.innerHTML;
              curApprovals[aIdx].className = newApprovals.className;
              curApprovals[aIdx].setAttribute('title', newApprovals.getAttribute('title') || '');
            }
          }

          var newDisapprovals = newContainer.querySelector('[data-membership-notes-disapprovals="' + requestPk + '"]');
          var curDisapprovals = currentContainer.querySelectorAll('[data-membership-notes-disapprovals="' + requestPk + '"]');
          if (newDisapprovals && curDisapprovals && curDisapprovals.length) {
            for (var dIdx = 0; dIdx < curDisapprovals.length; dIdx += 1) {
              curDisapprovals[dIdx].innerHTML = newDisapprovals.innerHTML;
              curDisapprovals[dIdx].className = newDisapprovals.className;
              curDisapprovals[dIdx].setAttribute('title', newDisapprovals.getAttribute('title') || '');
            }
          }

          setDetailsLoaded(true);
          if (message) {
            message.value = '';
          }
          scrollToBottomSoon();
        })
        .catch(function (err) {
          var box = document.getElementById('membership-notes-error-' + requestPk);
          var text = document.getElementById('membership-notes-error-text-' + requestPk);
          if (box) {
            var msg = err && err.message ? String(err.message) : '';
            if (!msg || msg === 'Failed') {
              msg = 'Could not post note right now. Please try again.';
            }
            if (text) {
              text.textContent = msg;
            }
            box.classList.remove('d-none');
          }
        })
        .finally(function () {
          var btns = form.querySelectorAll('button');
          for (var j = 0; j < btns.length; j += 1) btns[j].disabled = false;
        });
    }

    if (collapseBtn && card) {
      card.addEventListener('expanded.lte.cardwidget', function () {
        handleOpen();
      });
      card.addEventListener('collapsed.lte.cardwidget', function () {
        setOpenState(false);
      });

      collapseBtn.addEventListener('click', function () {
        var wasCollapsed = card.classList.contains('collapsed-card');
        setTimeout(function () {
          var isOpen = !card.classList.contains('collapsed-card');
          setOpenState(isOpen);
          if (wasCollapsed && isOpen) {
            handleOpen();
          }
        }, 0);
      });
    }

    if (header && collapseBtn) {
      header.addEventListener('click', function (e) {
        if (!e) return;
        var target = e.target;
        if (target && target.closest && target.closest('.card-tools')) return;
        var wasCollapsed = card && card.classList.contains('collapsed-card');
        collapseBtn.click();
        if (wasCollapsed) {
          handleOpen();
        }
      });

      header.addEventListener('keydown', function (e) {
        if (!e) return;
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          var wasCollapsed = card && card.classList.contains('collapsed-card');
          collapseBtn.click();
          if (wasCollapsed) {
            handleOpen();
          }
        }
      });
    }

    if (form) {
      form.addEventListener('submit', function (e) {
        if (!e) return;
        e.preventDefault();
        ajaxSubmit('message');
      });

      var voteButtons = form.querySelectorAll('[data-note-action]');
      for (var voteIndex = 0; voteIndex < voteButtons.length; voteIndex += 1) {
        voteButtons[voteIndex].addEventListener('click', function (e) {
          if (!e || !e.currentTarget) return;
          var action = e.currentTarget.getAttribute('data-note-action') || 'message';
          ajaxSubmit(action);
        });
      }
    }

    if (message) {
      message.addEventListener('keydown', function (e) {
        if (!e) return;
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          ajaxSubmit('message');
        }
      });
    }

    var errorClose = container.querySelector('[data-membership-notes-error-close="' + requestPk + '"]');
    if (errorClose) {
      errorClose.addEventListener('click', function () {
        var box = document.getElementById('membership-notes-error-' + requestPk);
        var text = document.getElementById('membership-notes-error-text-' + requestPk);
        if (text) text.textContent = '';
        if (box) box.classList.add('d-none');
      });
    }

    loadNoteSummary();
    ensureDesiredOpenState();
    if (!ensureOpenWidgetDetailsLoaded()) {
      scrollToBottomSoon();
      scheduleOpenWidgetDetailsRecovery(10);
    }
  };
})();
