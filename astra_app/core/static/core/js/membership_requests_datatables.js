(function (window, document) {
  'use strict';

  function escapeHtml(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function escapeHtmlWithLineBreaks(value) {
    if (window.AstraMembershipNotes && typeof window.AstraMembershipNotes.escapeHtmlWithLineBreaks === 'function') {
      return window.AstraMembershipNotes.escapeHtmlWithLineBreaks(value);
    }
    return escapeHtml(value).replace(/\r\n|\r|\n/g, '<br>');
  }

  function markSummaryUnavailable(container, requestId) {
    if (window.AstraMembershipNotes && typeof window.AstraMembershipNotes.markSummaryUnavailable === 'function') {
      window.AstraMembershipNotes.markSummaryUnavailable(container, requestId);
      return;
    }

    var countElement = container.querySelector('[data-membership-notes-count="' + requestId + '"]');
    if (!countElement) return;
    countElement.textContent = '!';
    countElement.className = 'badge badge-warning';
    countElement.setAttribute('title', 'Note summary unavailable');
  }

  function currentNextUrl() {
    return window.location.pathname + window.location.search;
  }

  function currentRouteState() {
    var url = new window.URL(window.location.href);
    return {
      filter: String(url.searchParams.get('filter') || 'all'),
      pending_page: parseInt(String(url.searchParams.get('pending_page') || '1'), 10) || 1,
      on_hold_page: parseInt(String(url.searchParams.get('on_hold_page') || '1'), 10) || 1
    };
  }

  function ensureNextInput(form) {
    if (!form) return;
    var nextInput = form.querySelector('input[name="next"]');
    if (nextInput) {
      nextInput.value = currentNextUrl();
    }
  }

  function parseBooleanAttribute(value) {
    return String(value || '').toLowerCase() === 'true';
  }

  function replaceTemplateToken(template, token, value) {
    return String(template || '').split(String(token || '')).join(String(value || ''));
  }

  function formatIsoDateTime(isoValue) {
    if (!isoValue) return '';
    var date = new window.Date(isoValue);
    if (Number.isNaN(date.getTime())) return '';
    var year = date.getFullYear();
    var month = String(date.getMonth() + 1).padStart(2, '0');
    var day = String(date.getDate()).padStart(2, '0');
    var hours = String(date.getHours()).padStart(2, '0');
    var minutes = String(date.getMinutes()).padStart(2, '0');
    return year + '-' + month + '-' + day + ' ' + hours + ':' + minutes;
  }

  function formatRelativeAgo(isoValue) {
    if (!isoValue) return '';
    var date = new window.Date(isoValue);
    if (Number.isNaN(date.getTime())) return '';
    var diffMs = Math.max(0, window.Date.now() - date.getTime());
    var minuteMs = 60 * 1000;
    var hourMs = 60 * minuteMs;
    var dayMs = 24 * hourMs;
    if (diffMs < hourMs) {
      var minutes = Math.max(1, Math.floor(diffMs / minuteMs));
      return minutes + ' minute' + (minutes === 1 ? '' : 's') + ' ago';
    }
    if (diffMs < dayMs) {
      var hours = Math.max(1, Math.floor(diffMs / hourMs));
      return hours + ' hour' + (hours === 1 ? '' : 's') + ' ago';
    }
    var days = Math.max(1, Math.floor(diffMs / dayMs));
    return days + ' day' + (days === 1 ? '' : 's') + ' ago';
  }

  function buildTargetHtml(target, routes) {
    if (!target) return '';
    var label = escapeHtml(target.label || '');
    if (target.kind === 'organization' && target.deleted) {
      return '<span>' + label + '</span> <span class="text-muted">(deleted)</span>';
    }
    var secondaryClass = target.kind === 'user' ? 'text-muted small' : 'text-muted';
    var secondary = target.secondary_label ? ' <span class="' + secondaryClass + '">(' + escapeHtml(target.secondary_label) + ')</span>' : '';
    var href = '';
    if (target.kind === 'user' && target.username) {
      href = replaceTemplateToken(routes.userProfileTemplate, '__username__', target.username);
    }
    if (target.kind === 'organization' && target.organization_id) {
      href = replaceTemplateToken(routes.organizationDetailTemplate, routes.requestIdSentinel, target.organization_id);
    }
    if (href) {
      return '<a href="' + escapeHtml(href) + '">' + label + secondary + '</a>';
    }
    if (target.deleted) {
      return label + secondary + ' <span class="text-muted">(deleted)</span>';
    }
    return label + secondary;
  }

  function buildRequestedByHtml(row, routes) {
    if (!row || !row.requested_by || !row.requested_by.show) return '';
    var requestedBy = row.requested_by;
    var label = requestedBy.full_name
      ? escapeHtml(requestedBy.full_name) + ' <span class="text-muted">(' + escapeHtml(requestedBy.username) + ')</span>'
      : escapeHtml(requestedBy.username);
    var href = requestedBy.username ? replaceTemplateToken(routes.userProfileTemplate, '__username__', requestedBy.username) : '';
    return '<div class="text-muted small">Requested by: <a href="' + escapeHtml(href) + '">' + label + '</a>' +
      (requestedBy.deleted ? ' <span class="text-muted">(deleted)</span>' : '') +
      '</div>';
  }

  function buildResponsesHtml(row) {
    var responses = row && row.responses ? row.responses : [];
    if (!responses.length) return '';
    var html = '<details class="mt-2" open><summary class="small text-muted">Request responses</summary><div class="mt-2">';
    responses.forEach(function (item) {
      html += '<div class="small text-muted font-weight-bold">' + escapeHtml(item.question || '') + '</div>';
      html += '<div class="small" style="white-space: pre-wrap;">' + String(item.answer_html || '') + '</div>';
    });
    html += '</div></details>';
    return html;
  }

  function buildEmailDetailModalId(requestId, contactedEmail) {
    return 'membership-email-modal-' + escapeHtml(requestId) + '-' + escapeHtml(contactedEmail.email_id || '');
  }

  function buildEmailDetailsModalHtml(requestId, contactedEmail) {
    if (!contactedEmail) return '';
    var modalId = buildEmailDetailModalId(requestId, contactedEmail);
    var html = '';
    html += '<div class="modal fade" id="' + modalId + '" tabindex="-1" role="dialog" aria-hidden="true" aria-labelledby="' + modalId + '-title">';
    html += '<div class="modal-dialog modal-lg" role="document"><div class="modal-content">';
    html += '<div class="modal-header"><h5 class="modal-title" id="' + modalId + '-title">Email details</h5>';
    html += '<button type="button" class="close" data-dismiss="modal" aria-label="Close" title="Close email details"><span aria-hidden="true">&times;</span></button></div>';
    html += '<div class="modal-body"><dl class="row mb-0">';
    html += '<dt class="col-sm-3">To</dt><dd class="col-sm-9">' + escapeHtml((contactedEmail.to || []).join(', ')) + '</dd>';
    html += '<dt class="col-sm-3">Subject</dt><dd class="col-sm-9">' + escapeHtml(contactedEmail.subject || '') + '</dd>';
    if (contactedEmail.from_email) {
      html += '<dt class="col-sm-3">From</dt><dd class="col-sm-9">' + escapeHtml(contactedEmail.from_email) + '</dd>';
    }
    if (contactedEmail.cc && contactedEmail.cc.length) {
      html += '<dt class="col-sm-3">CC</dt><dd class="col-sm-9">' + escapeHtml(contactedEmail.cc.join(', ')) + '</dd>';
    }
    if (contactedEmail.bcc && contactedEmail.bcc.length) {
      html += '<dt class="col-sm-3">BCC</dt><dd class="col-sm-9">' + escapeHtml(contactedEmail.bcc.join(', ')) + '</dd>';
    }
    if (contactedEmail.reply_to) {
      html += '<dt class="col-sm-3">Reply-To</dt><dd class="col-sm-9">' + escapeHtml(contactedEmail.reply_to) + '</dd>';
    }
    html += '<dt class="col-sm-3">Recipient delivery summary</dt><dd class="col-sm-9">' + escapeHtml(contactedEmail.recipient_delivery_summary || '');
    if (contactedEmail.recipient_delivery_summary_note) {
      html += '<div class="text-muted small">' + escapeHtml(contactedEmail.recipient_delivery_summary_note) + '</div>';
    }
    html += '</dd></dl>';
    if (contactedEmail.headers && contactedEmail.headers.length) {
      html += '<div class="mt-3"><div class="text-muted small mb-1">Other headers</div><ul class="small mb-0">';
      contactedEmail.headers.forEach(function (header) {
        html += '<li><strong>' + escapeHtml(header[0] || '') + ':</strong> ' + escapeHtml(header[1] || '') + '</li>';
      });
      html += '</ul></div>';
    }
    html += '<hr><h6 class="text-muted">HTML</h6>';
    if (contactedEmail.html) {
      html += '<iframe srcdoc="' + escapeHtml(contactedEmail.html) + '" sandbox title="Email HTML preview" style="width: 100%; height: 320px; border: 1px solid #dee2e6; border-radius: .25rem;"></iframe>';
    } else {
      html += '<div class="border rounded p-2 bg-light text-muted small">No HTML content.</div>';
    }
    html += '<h6 class="text-muted mt-3">Plain text</h6><pre class="border rounded p-2 bg-light" style="max-height: 260px; overflow: auto; white-space: pre-wrap;">' + escapeHtml(contactedEmail.text || '') + '</pre>';
    html += '<hr><h6 class="text-muted">Delivery logs</h6>';
    if (contactedEmail.logs && contactedEmail.logs.length) {
      html += '<table class="table table-sm"><thead><tr><th style="width: 30%;">Time</th><th style="width: 20%;">Status</th><th>Message</th></tr></thead><tbody>';
      contactedEmail.logs.forEach(function (log) {
        html += '<tr><td>' + escapeHtml(log.date_display || '') + '</td><td>' + escapeHtml(log.status || '') + '</td><td>' + escapeHtml(log.message || '');
        if (log.exception_type) {
          html += '<div class="text-muted small">' + escapeHtml(log.exception_type) + '</div>';
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
  }

  function buildNotesGroupsHtml(groups, requestId) {
    if (!groups || !groups.length) {
      return {
        groupsHtml: '<div class="text-muted small">No notes yet.</div>',
        modalsHtml: ''
      };
    }

    var html = '';
    var modalsHtml = '';
    groups.forEach(function (group, index) {
      var sideClass = group.is_self ? ' right' : '';
      html += '<div class="direct-chat-msg' + (index + 1 < groups.length ? ' mb-3' : '') + sideClass + '" data-membership-notes-group-username="' + escapeHtml(group.username || '') + '">';
      html += '<div class="direct-chat-infos clearfix">';
      if (group.is_self) {
        html += '<span class="direct-chat-name float-right">' + escapeHtml(group.display_username || group.username || '') + '</span>';
        html += '<span class="direct-chat-timestamp float-left">' + escapeHtml(group.timestamp_display || '') + '</span>';
      } else {
        html += '<span class="direct-chat-name float-left">' + escapeHtml(group.display_username || group.username || '') + '</span>';
        html += '<span class="direct-chat-timestamp float-right">' + escapeHtml(group.timestamp_display || '') + '</span>';
      }
      html += '</div>';

      if (group.avatar_url) {
        html += '<img class="direct-chat-img' + (group.avatar_kind === 'user' ? ' img-circle' : '') + '" src="' + escapeHtml(group.avatar_url) + '" alt="' + (group.avatar_kind === 'user' ? 'User Avatar' : 'Astra Custodia') + '"' + (group.avatar_kind === 'user' ? ' style="object-fit:cover;"' : ' style="object-fit: cover; background: #fff;"') + '>';
      } else {
        html += '<img class="direct-chat-img" src="/static/core/images/almalinux-logo.svg" alt="user image">';
      }

      html += '<div class="membership-notes-bubbles">';
      (group.entries || []).forEach(function (entry) {
        if (entry.kind === 'action') {
          html += '<div class="direct-chat-text bg-light membership-notes-bubble" style="' + escapeHtml(entry.bubble_style || '') + ' border: 1px dashed rgba(0,0,0,0.15);">';
          html += '<i class="fas ' + escapeHtml(entry.icon || 'fa-bolt') + ' mr-1"></i> ' + escapeHtml(entry.label || '');
          if (entry.contacted_email) {
            var modalId = buildEmailDetailModalId(requestId, entry.contacted_email);
            html += '<button type="button" class="btn btn-link btn-sm p-0 ml-2" data-toggle="modal" data-target="#' + modalId + '" aria-label="View email">View email</button>';
            modalsHtml += buildEmailDetailsModalHtml(requestId, entry.contacted_email);
          }
          if (entry.request_resubmitted_diff_rows && entry.request_resubmitted_diff_rows.length) {
            html += '<div class="mt-2" data-request-resubmitted-note-id="' + escapeHtml(entry.note_id || '') + '">';
            entry.request_resubmitted_diff_rows.forEach(function (diffRow) {
              html += '<details class="mt-1" data-request-resubmitted-question="' + escapeHtml(diffRow.question || '') + '">';
              html += '<summary>' + escapeHtml(diffRow.question || '') + '</summary>';
              html += '<div class="small text-muted mt-2">Previous response</div>';
              html += '<div data-request-resubmitted-old>' + escapeHtmlWithLineBreaks(diffRow.old_value || '') + '</div>';
              html += '<div class="small text-muted mt-2">Updated response</div>';
              html += '<div data-request-resubmitted-new>' + escapeHtmlWithLineBreaks(diffRow.new_value || '') + '</div>';
              html += '</details>';
            });
            html += '</div>';
          }
          html += '</div>';
          return;
        }

        if (!entry.is_self && entry.bubble_style) {
          var bubbleStyle = escapeHtml(entry.bubble_style || '');
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
  }

  function buildCompactNotesHtml(row, section, routes, noteConfig) {
    if (!noteConfig || !noteConfig.canView) return '';

    var requestId = row.request_id;
    var canWrite = !!noteConfig.canWrite;
    var canVote = !!noteConfig.canVote;
    var notePostUrl = replaceTemplateToken(noteConfig.postTemplate, routes.requestIdSentinel, requestId);
    var noteSummaryUrl = replaceTemplateToken(noteConfig.noteSummaryTemplate, routes.requestIdSentinel, requestId);
    var noteDetailUrl = replaceTemplateToken(noteConfig.noteDetailTemplate, routes.requestIdSentinel, requestId);
    var html = '';
    html += '<div id="membership-notes-container-' + escapeHtml(requestId) + '" data-membership-notes-container="' + escapeHtml(requestId) + '" data-membership-notes-default-open="0" data-membership-notes-refresh-section="' + escapeHtml(section) + '" data-membership-notes-summary-url="' + escapeHtml(noteSummaryUrl) + '" data-membership-notes-detail-url="' + escapeHtml(noteDetailUrl) + '" data-membership-notes-details-loaded="false">';
    html += '<div id="membership-notes-card-' + escapeHtml(requestId) + '" class="card card-primary card-outline direct-chat direct-chat-primary mb-0 collapsed-card" data-membership-notes-card="' + escapeHtml(requestId) + '">';
    html += '<div class="card-header membership-notes-header-compact" data-membership-notes-header="' + escapeHtml(requestId) + '" role="button" tabindex="0" aria-label="Toggle membership notes" style="cursor: pointer;">';
    html += '<h3 class="card-title membership-notes-title text-sm text-truncate">Membership Committee Notes</h3>';
    html += '<div class="card-tools">';
    html += '<span data-toggle="tooltip" title="Loading note summary" class="badge badge-primary" data-membership-notes-count="' + escapeHtml(requestId) + '">...</span>';
    html += '<span class="badge badge-success" title="Approvals" data-membership-notes-approvals="' + escapeHtml(requestId) + '"><i class="fas fa-thumbs-up"></i> ...</span>';
    html += '<span class="badge badge-danger" title="Disapprovals" data-membership-notes-disapprovals="' + escapeHtml(requestId) + '"><i class="fas fa-thumbs-down"></i> ...</span>';
    html += '<button type="button" class="btn btn-tool" data-card-widget="collapse" aria-label="Collapse" title="Expand or collapse notes" data-membership-notes-collapse="' + escapeHtml(requestId) + '"><i class="fas fa-plus"></i></button>';
    html += '</div></div>';
    html += '<div class="card-body"><div class="direct-chat-messages" data-membership-notes-messages="' + escapeHtml(requestId) + '" style="max-height: 260px;">';
    html += '<div class="text-muted small">Expand to load notes.</div>';
    html += '</div></div>';
    html += '<div class="card-footer">';
    html += '<div id="membership-notes-error-' + escapeHtml(requestId) + '" class="alert alert-danger py-2 px-3 mb-2 d-none" role="alert" aria-live="polite"><div class="d-flex align-items-start justify-content-between" style="gap: .75rem;"><div id="membership-notes-error-text-' + escapeHtml(requestId) + '"></div><button type="button" class="close" aria-label="Dismiss" title="Dismiss error message" data-membership-notes-error-close="' + escapeHtml(requestId) + '"><span aria-hidden="true">&times;</span></button></div></div>';
    if (canWrite) {
      html += '<form id="membership-notes-form-' + escapeHtml(requestId) + '" method="post" action="' + escapeHtml(notePostUrl) + '" data-membership-notes-form="' + escapeHtml(requestId) + '">';
      html += '<input type="hidden" name="csrfmiddlewaretoken" value="' + escapeHtml(getCsrfToken()) + '">';
      html += '<input type="hidden" name="next" value="' + escapeHtml(currentNextUrl()) + '">';
      html += '<input type="hidden" name="note_action" value="message">';
      html += '<div class="input-group"><textarea id="membership-notes-message-' + escapeHtml(requestId) + '" name="message" placeholder="Type a note..." class="form-control" rows="2"></textarea><div class="input-group-append"><button type="submit" class="btn btn-light border" title="Send (Ctrl+Enter)" aria-label="Send note"><i class="fas fa-paper-plane"></i></button></div></div>';
      if (canVote) {
        html += '<div class="d-flex w-100 mt-1" role="group" aria-label="Vote actions">';
        html += '<button type="button" class="btn btn-light border btn-sm flex-fill py-1" data-note-action="vote_approve" title="Vote to approve" aria-label="Vote approve"><i class="fas fa-thumbs-up text-success"></i></button>';
        html += '<button type="button" class="btn btn-light border btn-sm flex-fill py-1" data-note-action="vote_disapprove" title="Vote to disapprove" aria-label="Vote disapprove"><i class="fas fa-thumbs-down text-danger"></i></button>';
        html += '</div>';
      }
      html += '</form>';
    }
    html += '</div></div>';
    html += '<div data-membership-notes-modals="' + escapeHtml(requestId) + '"></div>';
    html += '</div>';
    return html;
  }

  function buildTypeCellHtml(row, includeResponses) {
    var html = escapeHtml(row.membership_type && row.membership_type.name ? row.membership_type.name : '');
    if (row.is_renewal) {
      html += ' <span class="badge badge-primary">Renewal</span>';
    }
    if (includeResponses) {
      html += buildResponsesHtml(row);
    }
    return html;
  }

  function buildRequesterCellHtml(row, section, routes, noteConfig) {
    return '<div>' + buildTargetHtml(row.target, routes) + '</div>' + buildRequestedByHtml(row, routes) + '<div class="mt-2">' + buildCompactNotesHtml(row, section, routes, noteConfig) + '</div>';
  }

  function buildActionButton(label, cssClass, targetId, actionUrl, modalTitle, row, bodyPrefix, bodySuffix, title, ariaLabel) {
    var attrs = [
      'type="button"',
      'class="' + cssClass + '"',
      'data-toggle="modal"',
      'data-target="#' + targetId + '"',
      'data-action-url="' + escapeHtml(actionUrl) + '"',
      'data-modal-title="' + escapeHtml(modalTitle) + '"',
      'data-request-id="' + escapeHtml(row.request_id) + '"',
      'data-request-target="' + escapeHtml(row.target && row.target.secondary_label ? row.target.secondary_label : (row.target ? row.target.label : '')) + '"',
      'data-membership-type="' + escapeHtml(row.membership_type && row.membership_type.name ? row.membership_type.name : '') + '"'
    ];
    if (title) attrs.push('title="' + escapeHtml(title) + '"');
    if (ariaLabel) attrs.push('aria-label="' + escapeHtml(ariaLabel) + '"');
    if (bodyPrefix) attrs.push('data-body-prefix="' + escapeHtml(bodyPrefix) + '"');
    if (bodySuffix) attrs.push('data-body-suffix="' + escapeHtml(bodySuffix) + '"');
    if ((bodyPrefix || bodySuffix) && row.target && row.target.secondary_label) {
      attrs.push('data-body-emphasis="' + escapeHtml(row.target.secondary_label) + '"');
    } else if ((bodyPrefix || bodySuffix) && row.target && row.target.label) {
      attrs.push('data-body-emphasis="' + escapeHtml(row.target.label) + '"');
    }
    return '<button ' + attrs.join(' ') + '>' + escapeHtml(label) + '</button>';
  }

  function buildActionUrl(row, actionName, routes) {
    var requestId = row && row.request_id ? row.request_id : '';
    if (!requestId) return '';
    if (actionName === 'approve') return replaceTemplateToken(routes.approveTemplate, routes.requestIdSentinel, requestId);
    if (actionName === 'approve_on_hold') return replaceTemplateToken(routes.approveOnHoldTemplate, routes.requestIdSentinel, requestId);
    if (actionName === 'reject') return replaceTemplateToken(routes.rejectTemplate, routes.requestIdSentinel, requestId);
    if (actionName === 'request_info') return replaceTemplateToken(routes.rfiTemplate, routes.requestIdSentinel, requestId);
    if (actionName === 'ignore') return replaceTemplateToken(routes.ignoreTemplate, routes.requestIdSentinel, requestId);
    return '';
  }

  function buildActionsHtml(row, routes) {
    var typeName = row.membership_type && row.membership_type.name ? row.membership_type.name : 'Membership';
    var html = '<div class="membership-request-actions membership-request-actions--list">';
    if (row.status === 'pending') {
      html += buildActionButton('Approve', 'btn btn-sm btn-success', 'shared-approve-modal', buildActionUrl(row, 'approve', routes), 'Approve ' + typeName + ' request', row, 'Approve ' + typeName + ' request from', '?', 'Approve this request', 'Approve');
    }
    if (row.status === 'on_hold') {
      html += buildActionButton('Approve', 'btn btn-sm btn-success', 'shared-approve-on-hold-modal', buildActionUrl(row, 'approve_on_hold', routes), 'Approve ' + typeName + ' request', row, '', '', 'Approve this on-hold request with committee override', 'Approve');
    }
    if (row.status === 'pending' || row.status === 'on_hold') {
      html += buildActionButton('Reject', 'btn btn-sm btn-danger', 'shared-reject-modal', buildActionUrl(row, 'reject', routes), 'Reject ' + typeName + ' request', row, '', '', 'Reject this request', 'Reject');
    }
    if (routes.canRequestInfo && row.status === 'pending') {
      html += buildActionButton('RFI', 'btn btn-sm btn-outline-primary', 'shared-rfi-modal', buildActionUrl(row, 'request_info', routes), 'Request information for ' + typeName + ' request', row, '', '', 'Request information and put on hold', 'Request for Information');
    }
    if (row.status === 'pending' || row.status === 'on_hold') {
      html += buildActionButton('Ignore', 'btn btn-sm btn-outline-secondary', 'shared-ignore-modal', buildActionUrl(row, 'ignore', routes), 'Ignore ' + typeName + ' request', row, 'Ignore ' + typeName + ' request from', '? This does not approve the membership, nor does it notify the user.', 'Ignore this request', 'Ignore');
    }
    html += '</div>';
    return html;
  }

  function buildRequestCellHtml(row, routes) {
    var detailUrl = replaceTemplateToken(routes.detailTemplate, routes.requestIdSentinel, row.request_id);
    return '<a href="' + escapeHtml(detailUrl) + '">Request #' + escapeHtml(row.request_id) + '</a><br/>' + escapeHtml(formatIsoDateTime(row.requested_at || ''));
  }

  function buildOnHoldCellHtml(row) {
    if (!row.on_hold_since) {
      return '<span class="text-muted">(unknown)</span>';
    }
    return escapeHtml(formatIsoDateTime(row.on_hold_since || '')) + '<div class="text-muted small">' + escapeHtml(formatRelativeAgo(row.on_hold_since || '')) + '</div>';
  }

  function getCsrfToken() {
    var input = document.querySelector('input[name="csrfmiddlewaretoken"]');
    return input ? String(input.value || '') : '';
  }

  function syncBulkUi(section) {
    var selectAll = document.getElementById(section === 'pending' ? 'select-all-requests' : 'select-all-requests-on-hold');
    var applyButton = document.getElementById(section === 'pending' ? 'bulk-apply' : 'bulk-apply-on-hold');
    var form = document.getElementById(section === 'pending' ? 'bulk-action-form' : 'bulk-action-form-on-hold');
    var selector = section === 'pending' ? '.request-checkbox--pending' : '.request-checkbox--on-hold';
    var checkboxes = Array.prototype.slice.call(document.querySelectorAll(selector));
    var anyChecked = checkboxes.some(function (checkbox) { return checkbox.checked; });
    var allChecked = checkboxes.length > 0 && checkboxes.every(function (checkbox) { return checkbox.checked; });
    if (selectAll) selectAll.checked = allChecked;
    if (applyButton && form) {
      var actionSelect = form.querySelector('select[name="bulk_action"]');
      var selectedAction = actionSelect ? String(actionSelect.value || '').trim() : '';
      applyButton.disabled = !(anyChecked && selectedAction);
    }
  }

  function columnCountForSection(section) {
    return section === 'pending' ? 5 : 6;
  }

  function loadingMessage(section) {
    return section === 'pending' ? 'Loading pending requests...' : 'Loading on-hold requests...';
  }

  function buildLoadingRow(section) {
    return '<tr><td colspan="' + columnCountForSection(section) + '" class="p-3 text-muted">' + loadingMessage(section) + '</td></tr>';
  }

  function footerInfoElement(section) {
    return document.getElementById(section === 'pending' ? 'membership-requests-pending-info' : 'membership-requests-on-hold-info');
  }

  function footerPagerElement(section) {
    return document.getElementById(section === 'pending' ? 'membership-requests-pending-pager' : 'membership-requests-on-hold-pager');
  }

  function sectionCountElement(section) {
    return document.getElementById(section === 'pending' ? 'membership-requests-pending-count' : 'membership-requests-on-hold-count');
  }

  function tableBodyElement(tableSelector) {
    return document.querySelector(tableSelector + ' tbody');
  }

  function setInitialLoadingState(section, tableSelector) {
    var tbody = tableBodyElement(tableSelector);
    var infoElement = footerInfoElement(section);
    var pagerElement = footerPagerElement(section);
    if (tbody) {
      tbody.innerHTML = buildLoadingRow(section);
    }
    if (infoElement) {
      infoElement.innerHTML = loadingMessage(section);
    }
    if (pagerElement) {
      pagerElement.innerHTML = '';
    }
  }

  function updateSectionCount(section, count) {
    var countElement = sectionCountElement(section);
    if (!countElement) {
      return;
    }
    countElement.innerHTML = (section === 'pending' ? 'Pending: ' : 'On hold: ') + String(count);
  }

  function updatePendingFilterOptions(filterMetadata) {
    var filterSelect = document.getElementById('requests-filter');
    if (!filterSelect || !filterMetadata || !Array.isArray(filterMetadata.options)) {
      return;
    }

    filterSelect.innerHTML = filterMetadata.options.map(function (option) {
      var selected = option && option.value === filterMetadata.selected ? ' selected' : '';
      return '<option value="' + escapeHtml(option.value || '') + '"' + selected + '>' +
        escapeHtml(option.label || '') + ' (' + escapeHtml(option.count || 0) + ')</option>';
    }).join('');

    if (filterMetadata.selected) {
      filterSelect.value = String(filterMetadata.selected);
    }
  }

  function paginationWindow(totalPages, currentPage) {
    if (totalPages <= 10) {
      return {
        pageNumbers: Array.from({ length: totalPages }, function (_unused, index) { return index + 1; }),
        showFirst: false,
        showLast: false
      };
    }

    var start = Math.max(1, currentPage - 2);
    var end = Math.min(totalPages, currentPage + 2);
    var pageNumbers = [];
    for (var pageNumber = start; pageNumber <= end; pageNumber += 1) {
      pageNumbers.push(pageNumber);
    }
    return {
      pageNumbers: pageNumbers,
      showFirst: pageNumbers.indexOf(1) === -1,
      showLast: pageNumbers.indexOf(totalPages) === -1
    };
  }

  function buildSectionPageUrl(section, pageNumber) {
    var url = new window.URL(window.location.href);
    var state = currentRouteState();
    var pendingPage = state.pending_page;
    var onHoldPage = state.on_hold_page;

    if (section === 'pending') {
      pendingPage = pageNumber;
    } else {
      onHoldPage = pageNumber;
    }

    if (state.filter && state.filter !== 'all') {
      url.searchParams.set('filter', state.filter);
    } else {
      url.searchParams.delete('filter');
    }

    if (pendingPage > 1) {
      url.searchParams.set('pending_page', String(pendingPage));
    } else {
      url.searchParams.delete('pending_page');
    }

    if (onHoldPage > 1) {
      url.searchParams.set('on_hold_page', String(onHoldPage));
    } else {
      url.searchParams.delete('on_hold_page');
    }

    return url.pathname + (url.search ? url.search : '');
  }

  function buildPagerLink(pageNumber, label, isActive, isDisabled, section) {
    var classes = ['page-item'];
    if (isActive) classes.push('active');
    if (isDisabled) classes.push('disabled');
    var href = isDisabled ? '#' : buildSectionPageUrl(section, pageNumber);
    return '<li class="' + classes.join(' ') + '"><a class="page-link" href="' + escapeHtml(href) + '">' + label + '</a></li>';
  }

  function updateFooter(section, requestStart, rowCount, filteredTotal) {
    var infoElement = footerInfoElement(section);
    var pagerElement = footerPagerElement(section);
    var pageSize = section === 'pending' ? 50 : 10;
    var currentPage = Math.floor(requestStart / pageSize) + 1;
    var totalPages = filteredTotal > 0 ? Math.ceil(filteredTotal / pageSize) : 0;

    if (infoElement) {
      if (filteredTotal > 0 && rowCount > 0) {
        infoElement.innerHTML = 'Showing ' + (requestStart + 1) + '–' + (requestStart + rowCount) + ' of ' + filteredTotal;
      } else {
        infoElement.innerHTML = '';
      }
    }

    if (!pagerElement) {
      return;
    }

    if (totalPages <= 1) {
      pagerElement.innerHTML = '';
      return;
    }

    var windowConfig = paginationWindow(totalPages, currentPage);
    var html = '';
    html += buildPagerLink(currentPage - 1, '&laquo;', false, currentPage <= 1, section).replace('<a class="page-link"', '<a class="page-link" aria-label="Previous"');

    if (windowConfig.showFirst) {
      html += buildPagerLink(1, '1', false, false, section);
      html += '<li class="page-item disabled"><span class="page-link">…</span></li>';
    }

    windowConfig.pageNumbers.forEach(function (pageNumber) {
      html += buildPagerLink(pageNumber, String(pageNumber), pageNumber === currentPage, false, section);
    });

    if (windowConfig.showLast) {
      html += '<li class="page-item disabled"><span class="page-link">…</span></li>';
      html += buildPagerLink(totalPages, String(totalPages), false, false, section);
    }

    html += buildPagerLink(currentPage + 1, '&raquo;', false, currentPage >= totalPages, section).replace('<a class="page-link"', '<a class="page-link" aria-label="Next"');
    pagerElement.innerHTML = html;
  }

  function buildEmptyRow(section, clearFilterUrl, columnCount) {
    if (section === 'pending' && currentRouteState().filter !== 'all') {
      return '<tr><td colspan="' + columnCount + '" class="p-3 text-muted">No requests match this filter. <a href="' + escapeHtml(clearFilterUrl || '/membership/requests/') + '">Clear filter</a></td></tr>';
    }
    if (section === 'pending') {
      return '<tr><td colspan="' + columnCount + '" class="p-3 text-muted">No pending requests.</td></tr>';
    }
    return '<tr><td colspan="' + columnCount + '" class="p-3 text-muted">No on-hold requests.</td></tr>';
  }

  function init() {
    var root = document.querySelector('[data-membership-requests-root]');
    if (!root || !window.jQuery || !window.jQuery.fn || !window.jQuery.fn.DataTable) {
      return;
    }

    var $ = window.jQuery;
    var selectedBySection = { pending: {}, on_hold: {} };
    var pendingApiUrl = String(root.getAttribute('data-membership-requests-pending-api-url') || '');
    var onHoldApiUrl = String(root.getAttribute('data-membership-requests-on-hold-api-url') || '');
    var clearFilterUrl = String(root.getAttribute('data-membership-requests-clear-filter-url') || '/membership/requests/');
    var routes = {
      requestIdSentinel: String(root.getAttribute('data-membership-request-id-sentinel') || '123456789'),
      detailTemplate: String(root.getAttribute('data-membership-request-detail-template') || ''),
      approveTemplate: String(root.getAttribute('data-membership-request-approve-template') || ''),
      approveOnHoldTemplate: String(root.getAttribute('data-membership-request-approve-on-hold-template') || ''),
      rejectTemplate: String(root.getAttribute('data-membership-request-reject-template') || ''),
      rfiTemplate: String(root.getAttribute('data-membership-request-rfi-template') || ''),
      ignoreTemplate: String(root.getAttribute('data-membership-request-ignore-template') || ''),
      userProfileTemplate: String(root.getAttribute('data-membership-user-profile-template') || ''),
      organizationDetailTemplate: String(root.getAttribute('data-membership-organization-detail-template') || ''),
      canRequestInfo: parseBooleanAttribute(root.getAttribute('data-membership-requests-can-request-info'))
    };
    var noteConfig = {
      postTemplate: String(root.getAttribute('data-membership-request-note-add-template') || ''),
      noteSummaryTemplate: String(root.getAttribute('data-membership-request-note-summary-template') || ''),
      noteDetailTemplate: String(root.getAttribute('data-membership-request-note-detail-template') || ''),
      canView: parseBooleanAttribute(root.getAttribute('data-membership-requests-notes-can-view')),
      canWrite: parseBooleanAttribute(root.getAttribute('data-membership-requests-notes-can-write')),
      canVote: parseBooleanAttribute(root.getAttribute('data-membership-requests-notes-can-vote'))
    };
    var detailsLoadedByRequestId = {};

    function clearAllSelections() {
      selectedBySection.pending = {};
      selectedBySection.on_hold = {};
      Array.prototype.slice.call(document.querySelectorAll('.request-checkbox--pending, .request-checkbox--on-hold')).forEach(function (checkbox) {
        checkbox.checked = false;
      });
      var pendingSelectAll = document.getElementById('select-all-requests');
      var onHoldSelectAll = document.getElementById('select-all-requests-on-hold');
      if (pendingSelectAll) pendingSelectAll.checked = false;
      if (onHoldSelectAll) onHoldSelectAll.checked = false;
      syncBulkUi('pending');
      syncBulkUi('on_hold');
    }

    function clearSectionSelections(section) {
      selectedBySection[section] = {};
      var selector = section === 'pending' ? '.request-checkbox--pending' : '.request-checkbox--on-hold';
      Array.prototype.slice.call(document.querySelectorAll(selector)).forEach(function (checkbox) {
        checkbox.checked = false;
      });
      var selectAll = document.getElementById(section === 'pending' ? 'select-all-requests' : 'select-all-requests-on-hold');
      if (selectAll) {
        selectAll.checked = false;
      }
      syncBulkUi(section);
    }

    function routeState() {
      return currentRouteState();
    }

    function replaceRouteState(filterValue, pendingPage, onHoldPage) {
      var url = new window.URL(window.location.href);
      if (filterValue && filterValue !== 'all') {
        url.searchParams.set('filter', filterValue);
      } else {
        url.searchParams.delete('filter');
      }
      if (pendingPage > 1) {
        url.searchParams.set('pending_page', String(pendingPage));
      } else {
        url.searchParams.delete('pending_page');
      }
      if (onHoldPage > 1) {
        url.searchParams.set('on_hold_page', String(onHoldPage));
      } else {
        url.searchParams.delete('on_hold_page');
      }
      window.history.replaceState({}, '', url.pathname + (url.search ? url.search : ''));
    }

    function noteContainer(requestId) {
      return document.getElementById('membership-notes-container-' + requestId);
    }

    function applyNoteSummary(requestId, summary) {
      var container = noteContainer(requestId);
      if (!container || !summary) return;
      var currentVote = String(summary.current_user_vote || '');
      var countElement = container.querySelector('[data-membership-notes-count="' + requestId + '"]');
      var approvalsElement = container.querySelector('[data-membership-notes-approvals="' + requestId + '"]');
      var disapprovalsElement = container.querySelector('[data-membership-notes-disapprovals="' + requestId + '"]');

      if (countElement) {
        countElement.textContent = String(summary.note_count || 0);
        countElement.setAttribute('title', String(summary.note_count || 0) + ' Messages');
      }
      if (approvalsElement) {
        approvalsElement.className = 'badge ' + (currentVote === 'approve' ? 'badge-warning' : 'badge-success');
        approvalsElement.innerHTML = '<i class="fas fa-thumbs-up"></i> ' + escapeHtml(summary.approvals || 0);
      }
      if (disapprovalsElement) {
        disapprovalsElement.className = 'badge ' + (currentVote === 'disapprove' ? 'badge-warning' : 'badge-danger');
        disapprovalsElement.innerHTML = '<i class="fas fa-thumbs-down"></i> ' + escapeHtml(summary.disapprovals || 0);
      }
    }

    function loadNoteSummary(requestId) {
      var container = noteContainer(requestId);
      if (!container) return;
      var summaryUrl = String(container.getAttribute('data-membership-notes-summary-url') || '').trim();
      if (!summaryUrl) return;

      window.fetch(summaryUrl, { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
        .then(function (response) {
          return response.json().then(function (payload) {
            return { ok: response.ok, payload: payload };
          });
        })
        .then(function (result) {
          if (!result.ok) {
            throw new Error((result.payload && result.payload.error) || 'Failed to load note summary.');
          }
          applyNoteSummary(requestId, result.payload);
        })
        .catch(function () {
          markSummaryUnavailable(container, requestId);
        });
    }

    function applyNoteDetails(requestId, details) {
      var container = noteContainer(requestId);
      if (!container) return;
      var messagesElement = container.querySelector('[data-membership-notes-messages="' + requestId + '"]');
      var modalsElement = container.querySelector('[data-membership-notes-modals="' + requestId + '"]');
      var notesGroups = buildNotesGroupsHtml(details && details.groups ? details.groups : [], requestId);
      if (messagesElement) {
        messagesElement.innerHTML = notesGroups.groupsHtml;
      }
      if (modalsElement) {
        modalsElement.innerHTML = notesGroups.modalsHtml;
      }
      container.setAttribute('data-membership-notes-details-loaded', 'true');
      detailsLoadedByRequestId[String(requestId)] = true;
    }

    function loadNoteDetails(requestId) {
      var requestKey = String(requestId || '');
      var container = noteContainer(requestKey);
      if (!container) return;
      if (detailsLoadedByRequestId[requestKey]) return;

      var detailUrl = String(container.getAttribute('data-membership-notes-detail-url') || '').trim();
      var messagesElement = container.querySelector('[data-membership-notes-messages="' + requestKey + '"]');
      if (!detailUrl) return;

      detailsLoadedByRequestId[requestKey] = true;
      if (messagesElement) {
        messagesElement.innerHTML = '<div class="text-muted small">Loading notes...</div>';
      }

      window.fetch(detailUrl, { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
        .then(function (response) {
          return response.json().then(function (payload) {
            return { ok: response.ok, payload: payload };
          });
        })
        .then(function (result) {
          if (!result.ok) {
            throw new Error((result.payload && result.payload.error) || 'Failed to load notes.');
          }
          applyNoteDetails(requestKey, result.payload);
        })
        .catch(function (error) {
          detailsLoadedByRequestId[requestKey] = false;
          container.setAttribute('data-membership-notes-details-loaded', 'false');
          if (messagesElement) {
            messagesElement.innerHTML = '<div class="text-danger small">' + escapeHtml(error.message || 'Failed to load notes.') + '</div>';
          }
        });
    }

    function installNoteDetailLoader(requestId) {
      var requestKey = String(requestId || '');
      var container = noteContainer(requestKey);
      if (!container || container.getAttribute('data-membership-notes-lazy-installed') === 'true') {
        return;
      }
      container.setAttribute('data-membership-notes-lazy-installed', 'true');

      var card = document.getElementById('membership-notes-card-' + requestKey);
      var collapseBtn = container.querySelector('[data-membership-notes-collapse="' + requestKey + '"]');

      function loadDetailsAfterExpandIntent() {
        var start = window.Date.now();

        function checkExpandedState() {
          if (card && !card.classList.contains('collapsed-card')) {
            loadNoteDetails(requestKey);
            return;
          }
          if (window.Date.now() - start <= 2000) {
            window.setTimeout(checkExpandedState, 50);
          }
        }

        if (!card || !card.classList.contains('collapsed-card')) {
          return;
        }

        window.setTimeout(checkExpandedState, 0);
      }

      if (card) {
        card.addEventListener('expanded.lte.cardwidget', function () {
          loadNoteDetails(requestKey);
        });
      }
      if (collapseBtn) {
        collapseBtn.addEventListener('click', loadDetailsAfterExpandIntent);
      }
    }

    function noteInit(section) {
      var selector = section === 'pending' ? '.request-checkbox--pending' : '.request-checkbox--on-hold';
      Array.prototype.slice.call(document.querySelectorAll(selector)).forEach(function (checkbox) {
        var rowId = String(checkbox.value || '');
        if (window.AstraMembershipNotes && typeof window.AstraMembershipNotes.init === 'function') {
          window.AstraMembershipNotes.init(rowId);
          return;
        }

        var container = noteContainer(rowId);
        if (!container || container.getAttribute('data-membership-notes-fallback-initialized') === 'true') {
          return;
        }
        container.setAttribute('data-membership-notes-fallback-initialized', 'true');
        loadNoteSummary(rowId);
        installNoteDetailLoader(rowId);
      });
    }

    function rowRenderer(section, row) {
      var checkboxClass = section === 'pending' ? 'request-checkbox request-checkbox--pending' : 'request-checkbox request-checkbox--on-hold';
      var formId = section === 'pending' ? 'bulk-action-form' : 'bulk-action-form-on-hold';
      var checked = !!selectedBySection[section][String(row.request_id)];
      var html = '<tr>';
      html += '<td class="text-center align-top" style="width: 40px;"><input type="checkbox" class="' + checkboxClass + '" name="selected" value="' + escapeHtml(row.request_id) + '" form="' + formId + '" aria-label="Select request"' + (checked ? ' checked' : '') + '></td>';
      html += '<td class="text-muted text-nowrap" style="width: 1%;">' + buildRequestCellHtml(row, routes) + '</td>';
      html += '<td style="width: 30%;">' + buildRequesterCellHtml(row, section, routes, noteConfig) + '</td>';
      html += '<td>' + buildTypeCellHtml(row, section === 'pending') + '</td>';
      if (section === 'on_hold') {
        html += '<td class="text-nowrap" style="width: 1%;">' + buildOnHoldCellHtml(row) + '</td>';
      }
      html += '<td class="text-right" style="width: 15%;">' + buildActionsHtml(row, routes) + '</td>';
      html += '</tr>';
      return html;
    }

    function installTable(section, tableSelector, apiUrl, pageSize, orderName) {
      setInitialLoadingState(section, tableSelector);

      var table = $(tableSelector).DataTable({
        serverSide: true,
        processing: true,
        autoWidth: false,
        searching: false,
        lengthChange: false,
        info: false,
        ordering: false,
        pageLength: pageSize,
        displayStart: (section === 'pending' ? routeState().pending_page - 1 : routeState().on_hold_page - 1) * pageSize,
        dom: 't',
        language: {
          processing: '<span class="text-muted small">' + loadingMessage(section) + '</span>',
          emptyTable: '',
          zeroRecords: ''
        },
        ajax: function (data, callback) {
          var params = new window.URLSearchParams();
          params.set('draw', String(data.draw || 0));
          params.set('start', String(data.start || 0));
          params.set('length', String(pageSize));
          params.set('search[value]', '');
          params.set('search[regex]', 'false');
          params.set('order[0][column]', '0');
          params.set('order[0][dir]', 'asc');
          params.set('order[0][name]', orderName);
          params.set('columns[0][data]', 'request_id');
          params.set('columns[0][name]', orderName);
          params.set('columns[0][searchable]', 'true');
          params.set('columns[0][orderable]', 'true');
          params.set('columns[0][search][value]', '');
          params.set('columns[0][search][regex]', 'false');
          if (section === 'pending') {
            params.set('queue_filter', routeState().filter || 'all');
          }
          window.fetch(apiUrl + '?' + params.toString(), { headers: { Accept: 'application/json' }, credentials: 'same-origin' })
            .then(function (response) {
              return response.json().then(function (payload) {
                return { ok: response.ok, payload: payload };
              });
            })
            .then(function (result) {
              if (!result.ok) {
                throw new Error((result.payload && result.payload.error) || 'Failed to load membership requests.');
              }
              callback(result.payload);
              updateSectionCount(section, Number(result.payload.recordsFiltered || 0));
              if (section === 'pending') {
                updatePendingFilterOptions(result.payload.pending_filter);
              }
              var tbody = tableBodyElement(tableSelector);
              if (!tbody) return;
              var rows = result.payload.data || [];
              var visibleRowIds = {};
              rows.forEach(function (row) {
                visibleRowIds[String(row.request_id)] = true;
              });
              selectedBySection[section] = Object.keys(selectedBySection[section]).reduce(function (nextSelected, requestId) {
                if (visibleRowIds[requestId]) {
                  nextSelected[requestId] = true;
                }
                return nextSelected;
              }, {});
              if (!rows.length) {
                tbody.innerHTML = buildEmptyRow(section, clearFilterUrl, section === 'pending' ? 5 : 6);
                updateFooter(section, Number(data.start || 0), 0, Number(result.payload.recordsFiltered || 0));
                syncBulkUi(section);
                return;
              }
              tbody.innerHTML = rows.map(function (row) { return rowRenderer(section, row); }).join('');
              updateFooter(section, Number(data.start || 0), rows.length, Number(result.payload.recordsFiltered || 0));
              noteInit(section);
              syncBulkUi(section);
            })
            .catch(function (error) {
              callback({ draw: Number(data.draw || 0), recordsTotal: 0, recordsFiltered: 0, data: [] });
              var tbody = tableBodyElement(tableSelector);
              if (tbody) {
                tbody.innerHTML = '<tr><td colspan="' + columnCountForSection(section) + '" class="p-3 text-danger">' + escapeHtml(error.message || 'Failed to load membership requests.') + '</td></tr>';
              }
              var infoElement = footerInfoElement(section);
              var pagerElement = footerPagerElement(section);
              if (infoElement) {
                infoElement.innerHTML = '';
              }
              if (pagerElement) {
                pagerElement.innerHTML = '';
              }
            });
        },
        columns: section === 'pending'
          ? [
              { data: 'request_id', width: '40px' },
              { data: 'request_id', width: '1%', type: 'string' },
              { data: 'target', width: '30%' },
              { data: 'membership_type' },
              { data: 'status', width: '15%' }
            ]
          : [
              { data: 'request_id', width: '40px' },
              { data: 'request_id', width: '1%', type: 'string' },
              { data: 'target', width: '30%' },
              { data: 'membership_type' },
              { data: 'on_hold_since', width: '1%' },
              { data: 'status', width: '15%' }
            ]
      });

      $(tableSelector).on('draw.dt', function () {
        var pendingPage = pendingTable ? pendingTable.page.info().page + 1 : routeState().pending_page;
        var onHoldPage = onHoldTable ? onHoldTable.page.info().page + 1 : routeState().on_hold_page;
        replaceRouteState(routeState().filter, pendingPage, onHoldPage);
        syncBulkUi(section);
        noteInit(section);
      });

      $(tableSelector).on('page.dt', function () {
        clearAllSelections();
      });

      return table;
    }

    var pendingTable = installTable('pending', '#membership-requests-pending-table', pendingApiUrl, 50, 'requested_at');
    var onHoldTable = installTable('on_hold', '#membership-requests-on-hold-table', onHoldApiUrl, 10, 'on_hold_at');

    document.addEventListener('change', function (event) {
      var target = event.target;
      if (!target) return;
      if (target.matches && target.matches('.request-checkbox--pending')) {
        if (target.checked) {
          selectedBySection.pending[String(target.value || '')] = true;
        } else {
          delete selectedBySection.pending[String(target.value || '')];
        }
        syncBulkUi('pending');
        return;
      }
      if (target.matches && target.matches('.request-checkbox--on-hold')) {
        if (target.checked) {
          selectedBySection.on_hold[String(target.value || '')] = true;
        } else {
          delete selectedBySection.on_hold[String(target.value || '')];
        }
        syncBulkUi('on_hold');
        return;
      }
      if (target.id === 'requests-filter') {
        var filterValue = String(target.value || 'all');
        clearAllSelections();
        replaceRouteState(filterValue, 1, 1);
        if (pendingTable) {
          pendingTable.ajax.reload(null, true);
        }
        if (onHoldTable) {
          onHoldTable.ajax.reload(null, true);
        }
      }
    });

    document.addEventListener('submit', function (event) {
      var form = event.target;
      if (!form || !form.matches) return;
      if (
        form.matches('#bulk-action-form') ||
        form.matches('#bulk-action-form-on-hold') ||
        form.closest('#shared-approve-modal, #shared-approve-on-hold-modal, #shared-reject-modal, #shared-rfi-modal, #shared-ignore-modal') ||
        form.matches('[data-membership-notes-form]')
      ) {
        ensureNextInput(form);
      }
      if (form.matches('#bulk-action-form')) {
        selectedBySection.pending = {};
      }
      if (form.matches('#bulk-action-form-on-hold')) {
        selectedBySection.on_hold = {};
      }
    }, true);

    document.addEventListener('astra:membership-notes-posted', function (event) {
      if (!event || !event.detail || !event.detail.section) return;
      if (typeof event.preventDefault === 'function') {
        event.preventDefault();
      }
      if (event.detail.section === 'pending' && pendingTable) {
        pendingTable.ajax.reload(null, false);
      }
      if (event.detail.section === 'on_hold' && onHoldTable) {
        onHoldTable.ajax.reload(null, false);
      }
    });

    syncBulkUi('pending');
    syncBulkUi('on_hold');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(window, document);