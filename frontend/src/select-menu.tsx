import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Check as CheckIcon, ChevronDown } from "lucide-react";

export function SelectMenu({
  value,
  onChange,
  options,
  ariaLabel,
  className = "field-select",
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  ariaLabel?: string;
  className?: string;
  disabled?: boolean;
}) {
  const selected = options.find((option) => option.value === value);
  return (
    <DropdownMenu.Root modal={false}>
      <DropdownMenu.Trigger asChild disabled={disabled}>
        <button type="button" className={className} aria-label={ariaLabel} disabled={disabled}>
          <span>{selected?.label ?? options[0]?.label ?? ""}</span>
          <ChevronDown size={15} aria-hidden />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="select-menu" align="start" sideOffset={6}>
          {options.map((option) => (
            <DropdownMenu.Item
              key={option.value}
              className="select-menu-item"
              data-current={option.value === value ? "true" : undefined}
              onSelect={() => onChange(option.value)}
            >
              <span>{option.label}</span>
              {option.value === value ? <CheckIcon size={14} aria-hidden /> : null}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}
