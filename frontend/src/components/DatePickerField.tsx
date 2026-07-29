import { useRef, type ChangeEventHandler, type InputHTMLAttributes } from 'react';
import { Calendar } from 'lucide-react';

export type DatePickerFieldProps = {
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  title?: string;
  'aria-label'?: string;
  id?: string;
  name?: string;
  min?: string;
  max?: string;
  required?: boolean;
  className?: string;
  inputProps?: Omit<
    InputHTMLAttributes<HTMLInputElement>,
    'type' | 'value' | 'onChange' | 'disabled' | 'className' | 'ref'
  >;
};

/**
 * 统一日期字段：隐藏原生微弱日历图标，右侧显式按钮打开选择器。
 * 新 UI 禁止再直接写 `<input type="date" />`，请用本组件。
 */
export function DatePickerField({
  value,
  onChange,
  disabled = false,
  title = '选择日期',
  'aria-label': ariaLabel = '打开日期选择',
  id,
  name,
  min,
  max,
  required,
  className,
  inputProps,
}: DatePickerFieldProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);

  const handleChange: ChangeEventHandler<HTMLInputElement> = (e) => {
    onChange(e.target.value);
  };

  const openPicker = () => {
    const el = inputRef.current;
    if (!el || el.disabled) return;
    try {
      if (typeof el.showPicker === 'function') {
        el.showPicker();
      } else {
        el.focus();
        el.click();
      }
    } catch {
      el.focus();
    }
  };

  return (
    <div className={['date-picker-field', className].filter(Boolean).join(' ')}>
      <input
        {...inputProps}
        ref={inputRef}
        id={id}
        name={name}
        type="date"
        className="date-picker-field__input"
        value={value}
        disabled={disabled}
        min={min}
        max={max}
        required={required}
        onChange={handleChange}
      />
      <button
        type="button"
        className="lookback-stepper__btn date-picker-field__btn"
        disabled={disabled}
        aria-label={ariaLabel}
        title={title}
        onClick={openPicker}
      >
        <Calendar size={16} strokeWidth={2.25} />
      </button>
    </div>
  );
}
