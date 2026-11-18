/** Hàng đợi bền trong IndexedDB: giữ đặc trưng và câu trả lời khi mất mạng, gửi bù khi có lại. */
const DB_NAME = "examguard";

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("windows")) db.createObjectStore("windows", { keyPath: "key" });
      if (!db.objectStoreNames.contains("answers")) db.createObjectStore("answers", { keyPath: "key" });
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

/** Có IndexedDB thì lưu bền; không có (chế độ riêng tư) thì giữ trong bộ nhớ. */
export class Outbox<T extends { key: string }> {
  private db: Promise<IDBDatabase | null>;
  private mem = new Map<string, T>();

  constructor(private store: "windows" | "answers") {
    this.db = open().catch(() => null);
  }

  private async tx(mode: IDBTransactionMode, fn: (s: IDBObjectStore) => IDBRequest | void): Promise<any> {
    const db = await this.db;
    if (!db) return undefined;
    return new Promise((resolve, reject) => {
      const t = db.transaction(this.store, mode);
      const r = fn(t.objectStore(this.store));
      t.oncomplete = () => resolve(r ? (r as IDBRequest).result : undefined);
      t.onerror = () => reject(t.error);
    });
  }

  async put(item: T): Promise<void> {
    this.mem.set(item.key, item);
    await this.tx("readwrite", (s) => { s.put(item); }).catch(() => {});
  }

  async all(prefix: string): Promise<T[]> {
    const fromDb: T[] = (await this.tx("readonly", (s) => s.getAll()).catch(() => undefined)) || [];
    const map = new Map<string, T>();
    for (const x of [...fromDb, ...this.mem.values()]) if (x.key.startsWith(prefix)) map.set(x.key, x);
    return [...map.values()].sort((a, b) => (a.key < b.key ? -1 : 1));
  }

  async remove(keys: string[]): Promise<void> {
    for (const k of keys) this.mem.delete(k);
    await this.tx("readwrite", (s) => { for (const k of keys) s.delete(k); }).catch(() => {});
  }
}

export const pad = (n: number) => String(n).padStart(8, "0");
