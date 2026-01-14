'use client';

interface SuccessBannerProps {
  message: string;
  onDismiss?: () => void;
}

export default function SuccessBanner({ message, onDismiss }: SuccessBannerProps) {
  return (
    <div className="alert alert-success">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <span>{message}</span>
        {onDismiss && (
          <button
            onClick={onDismiss}
            style={{
              background: 'none',
              border: 'none',
              fontSize: '18px',
              cursor: 'pointer',
              padding: '0 8px',
            }}
          >
            ×
          </button>
        )}
      </div>
    </div>
  );
}
