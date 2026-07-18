(() => {
  'use strict';

  const body = document.body;
  const sidebar = document.querySelector('[data-dashboard-sidebar]');
  const sidebarToggle = document.querySelector('[data-sidebar-toggle]');
  const sidebarClose = document.querySelector('[data-sidebar-close]');
  const sidebarBackdrop = document.querySelector('[data-sidebar-backdrop]');

  const setSidebar = (open) => {
    if (!sidebar) return;
    sidebar.classList.toggle('open', open);
    sidebarBackdrop?.classList.toggle('open', open);
    sidebarToggle?.setAttribute('aria-expanded', String(open));
    body.classList.toggle('menu-open', open);
    if (open) sidebar.querySelector('a, button')?.focus();
    else sidebarToggle?.focus();
  };

  sidebarToggle?.addEventListener('click', () => setSidebar(true));
  sidebarClose?.addEventListener('click', () => setSidebar(false));
  sidebarBackdrop?.addEventListener('click', () => setSidebar(false));

  const publicMenu = document.querySelector('[data-public-menu]');
  const publicMenuToggle = document.querySelector('[data-public-menu-toggle]');
  publicMenuToggle?.addEventListener('click', () => {
    const open = !publicMenu?.classList.contains('open');
    publicMenu?.classList.toggle('open', open);
    publicMenuToggle.setAttribute('aria-expanded', String(open));
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      if (sidebar?.classList.contains('open')) setSidebar(false);
      publicMenu?.classList.remove('open');
      publicMenuToggle?.setAttribute('aria-expanded', 'false');
      document.querySelectorAll('dialog[open]').forEach((dialog) => dialog.close());
    }
  });

  document.querySelectorAll('[data-password-toggle]').forEach((button) => {
    button.addEventListener('click', () => {
      const input = button.parentElement?.querySelector('input');
      if (!input) return;
      const willShow = input.type === 'password';
      input.type = willShow ? 'text' : 'password';
      button.setAttribute('aria-label', willShow ? 'Hide password' : 'Show password');
      const icon = button.querySelector('[aria-hidden="true"]');
      if (icon) icon.textContent = willShow ? '🙈' : '👁';
    });
  });

  document.querySelectorAll('[data-strength-input]').forEach((input) => {
    const form = input.closest('form');
    const fill = form?.querySelector('[data-strength-fill]');
    const text = form?.querySelector('[data-strength-text]');
    const rules = {
      length: (value) => value.length >= 8,
      upper: (value) => /[A-Z]/.test(value),
      lower: (value) => /[a-z]/.test(value),
      number: (value) => /\d/.test(value),
      symbol: (value) => /[^A-Za-z0-9]/.test(value),
    };
    const labels = ['Password strength', 'Very weak', 'Weak', 'Fair', 'Strong', 'Very strong'];
    const colors = ['#ef4444', '#ef4444', '#f97316', '#f59e0b', '#22c55e', '#16a34a'];

    const updateStrength = () => {
      const value = input.value;
      const results = Object.entries(rules).map(([name, test]) => {
        const met = test(value);
        form?.querySelector(`[data-rule="${name}"]`)?.classList.toggle('met', met);
        return met;
      });
      const score = results.filter(Boolean).length;
      if (fill) {
        fill.style.width = `${score * 20}%`;
        fill.style.background = colors[score];
      }
      if (text) text.textContent = labels[score];
    };

    input.addEventListener('input', updateStrength);
    updateStrength();
  });

  document.querySelectorAll('[data-dismiss-flash]').forEach((button) => {
    button.addEventListener('click', () => button.closest('.flash')?.remove());
  });

  document.querySelectorAll('[data-history-back]').forEach((button) => {
    button.addEventListener('click', () => window.history.back());
  });
  document.querySelectorAll('[data-page-reload]').forEach((button) => {
    button.addEventListener('click', () => window.location.reload());
  });

  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (!window.confirm(form.dataset.confirm || 'Continue with this action?')) event.preventDefault();
    });
  });

  document.querySelectorAll('[data-dialog-open]').forEach((button) => {
    button.addEventListener('click', () => {
      const dialog = document.getElementById(button.dataset.dialogOpen || '');
      if (dialog instanceof HTMLDialogElement) dialog.showModal();
    });
  });

  document.querySelectorAll('[data-dialog-close]').forEach((button) => {
    button.addEventListener('click', () => button.closest('dialog')?.close());
  });

  document.querySelectorAll('dialog').forEach((dialog) => {
    dialog.addEventListener('click', (event) => {
      if (event.target === dialog) dialog.close();
    });
  });

  const protectedToggle = document.querySelector('input[name="is_protected"]');
  const pinField = document.querySelector('[data-protection-pin]');
  const updatePinVisibility = () => {
    if (!protectedToggle || !pinField) return;
    pinField.hidden = !protectedToggle.checked;
    const pinInput = pinField.querySelector('input');
    if (pinInput) pinInput.disabled = !protectedToggle.checked;
  };
  protectedToggle?.addEventListener('change', updatePinVisibility);
  updatePinVisibility();

  document.querySelectorAll('[data-mode-color]').forEach((element) => {
    const color = String(element.dataset.modeColor || '').trim().toLowerCase();
    if (/^#[0-9a-f]{6}$/.test(color)) element.style.setProperty('--mode-color', color);
  });

  const strictnessLabels = {
    1: 'Very flexible',
    2: 'Flexible',
    3: 'Balanced',
    4: 'Strict',
    5: 'Very strict',
  };
  document.querySelectorAll('[data-strictness-range]').forEach((range) => {
    const container = range.closest('.strictness-control');
    const label = container?.querySelector('[data-strictness-label]');
    const value = container?.querySelector('[data-strictness-value]');
    const updateStrictness = () => {
      const level = Math.min(5, Math.max(1, Number.parseInt(range.value, 10) || 3));
      if (label) label.textContent = strictnessLabels[level];
      if (value) value.textContent = String(level);
      range.setAttribute('aria-valuetext', strictnessLabels[level]);
      range.style.setProperty('--range-progress', `${(level - 1) * 25}%`);
    };
    range.addEventListener('input', updateStrictness);
    updateStrictness();
  });

  const schedulePreview = document.querySelector('[data-schedule-preview]');
  if (schedulePreview) {
    const dayNames = {
      mon: 'Mon',
      tue: 'Tue',
      wed: 'Wed',
      thu: 'Thu',
      fri: 'Fri',
      sat: 'Sat',
      sun: 'Sun',
    };
    const dayInputs = [...document.querySelectorAll('[data-schedule-day]')];
    const startInput = document.querySelector('[data-schedule-start]');
    const endInput = document.querySelector('[data-schedule-end]');
    const summary = schedulePreview.querySelector('[data-schedule-summary]');
    const detail = schedulePreview.querySelector('small');

    const formatTime = (time) => {
      if (!/^\d{2}:\d{2}$/.test(time || '')) return time || '';
      const [hourText, minute] = time.split(':');
      const hour = Number.parseInt(hourText, 10);
      const suffix = hour >= 12 ? 'PM' : 'AM';
      return `${hour % 12 || 12}:${minute} ${suffix}`;
    };

    const updateSchedulePreview = () => {
      const selectedDays = dayInputs.filter((input) => input.checked).map((input) => dayNames[input.value]);
      const start = startInput?.value || '';
      const end = endInput?.value || '';
      if (selectedDays.length && start && end) {
        const days = selectedDays.length === 7 ? 'Every day' : selectedDays.join(', ');
        if (summary) summary.textContent = `${days} | ${formatTime(start)} - ${formatTime(end)}`;
        if (detail) detail.textContent = 'This schedule will be stored for future extension automation.';
      } else if (selectedDays.length || start || end) {
        if (summary) summary.textContent = 'Complete the schedule';
        if (detail) detail.textContent = 'Choose at least one day and provide both a start and end time.';
      } else {
        if (summary) summary.textContent = 'No schedule set';
        if (detail) detail.textContent = 'Without a schedule, you can activate this mode at any time.';
      }
    };

    [...dayInputs, startInput, endInput].filter(Boolean).forEach((input) => {
      input.addEventListener('change', updateSchedulePreview);
      input.addEventListener('input', updateSchedulePreview);
    });
    updateSchedulePreview();
  }

  const closeMenusForDesktop = () => {
    if (window.matchMedia('(min-width: 821px)').matches) {
      sidebar?.classList.remove('open');
      sidebarBackdrop?.classList.remove('open');
      sidebarToggle?.setAttribute('aria-expanded', 'false');
      body.classList.remove('menu-open');
    }
  };
  window.addEventListener('resize', closeMenusForDesktop);
})();
