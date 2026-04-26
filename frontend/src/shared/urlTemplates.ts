export function fillUrlTemplate(template: string, placeholder: string, value: string | number): string {
  const encodedValue = encodeURIComponent(String(value));
  return template.split(placeholder).join(encodedValue);
}