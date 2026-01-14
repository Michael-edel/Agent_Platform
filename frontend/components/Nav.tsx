'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

export default function Nav() {
  const pathname = usePathname();

  const links = [
    { href: '/app/settings', label: 'Settings' },
    { href: '/app/inbox', label: 'Inbox' },
    { href: '/app/documents', label: 'Documents' },
    { href: '/app/invoices', label: 'Invoices' },
    { href: '/app/billing', label: 'Billing' },
  ];

  return (
    <nav className="nav">
      <div className="nav-container">
        <Link href="/app/documents" className="nav-link">
          <strong>CyberPlat</strong>
        </Link>
        {links.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            className={`nav-link ${pathname === link.href ? 'active' : ''}`}
          >
            {link.label}
          </Link>
        ))}
      </div>
    </nav>
  );
}
