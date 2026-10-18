import { showToast } from './toast';

type Field = HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement;

export function markFieldError(field: Field): void {
  field.setAttribute('aria-invalid', 'true');
}

export function clearFieldError(field: Field): void {
  if (field.validity.valid) field.removeAttribute('aria-invalid');
}

function fieldLabel(field: Field): string {
  const label = field.labels?.[0];
  const directText = label ? [...label.childNodes].filter(node => node.nodeType === Node.TEXT_NODE).map(node => node.textContent).join('').trim() : '';
  return directText || field.getAttribute('aria-label') || (document.documentElement.lang === 'en' ? 'this field' : '此项');
}

export function validateForm(form: HTMLFormElement): boolean {
  const invalid = [...form.elements].filter((element): element is Field =>
    (element instanceof HTMLInputElement || element instanceof HTMLSelectElement || element instanceof HTMLTextAreaElement) && element.willValidate && !element.validity.valid);
  if (!invalid.length) return true;
  invalid.forEach(markFieldError);
  const field = invalid[0], label = fieldLabel(field), english = document.documentElement.lang === 'en';
  let message = english ? `Please check ${label}.` : `请检查${label}的填写内容。`;
  if (field.validity.valueMissing) message = english ? `Please enter ${label}.` : `请填写${label}。`;
  else if (field.validity.tooShort && 'minLength' in field) message = english ? `${label} needs at least ${field.minLength} characters.` : `${label}至少需要 ${field.minLength} 个字符。`;
  else if (field instanceof HTMLInputElement && field.validity.rangeUnderflow) message = english ? `${label} must be at least ${field.min}.` : `${label}不能小于 ${field.min}。`;
  else if (field instanceof HTMLInputElement && field.validity.rangeOverflow) message = english ? `${label} must be at most ${field.max}.` : `${label}不能大于 ${field.max}。`;
  showToast(message, 'error');
  field.focus();
  return false;
}
