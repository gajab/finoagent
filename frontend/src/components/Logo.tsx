import React from 'react';

interface LogoProps {
  size?: number;
  className?: string;
}

/**
 * FinoAgent.ai brand logo — a pulse line trending upward with an arrow tip,
 * rendered as inline SVG so it stays crisp at any size and inherits theme colors.
 */
export function Logo({ size = 24, className = '' }: LogoProps) {
  return (
    <img
      src="/logo.png"
      alt="finoagent.ai Logo"
      width={size}
      height={size}
      className={className}
      style={{
        objectFit: 'contain',
        display: 'inline-block',
      }}
    />
  );
}
