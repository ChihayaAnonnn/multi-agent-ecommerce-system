export type Price = {
  sku_id: string;
  currency: string;
  amount_minor: number;
  version: number;
  read_at: string;
};
export type Availability = {
  status: "available" | "unknown" | "out_of_stock";
  available_quantity: number | null;
};
export type Product = {
  id: string;
  name: string;
  brand: string | null;
  category: { id: string; code: string; name: string };
  image_url: string | null;
  sku_id: string | null;
  price: Price | null;
  availability: Availability | null;
  purchasable: boolean;
  reason: string;
  simulated: boolean;
};
export type ProductPage = { items: Product[]; next_cursor: string | null };
export type ProductResolved = {
  items: Product[];
  missing_product_ids: string[];
};
export type ProductDetail = {
  id: string;
  name: string;
  description: string | null;
  brand: string | null;
  attributes: Record<string, unknown>;
  category: { name: string };
  media: { url: string; media_type: string }[];
  skus: {
    id: string;
    sku_code: string;
    status: string;
    selected_values: { display_value: string }[];
  }[];
};
export type Candidate = {
  product_id: string;
  sku_id: string;
  name?: string;
  explanation?: string;
};
export type Proposal = {
  action_id: string;
  kind: string;
  summary?: {
    name: string;
    unit_amount_minor: number;
    final_quantity: number;
    sku_options?: { display_value: string }[];
  };
  items?: { name: string; unit_amount_minor: number; quantity: number }[];
  current_subtotal?: number;
};
export type Cart = {
  version: number;
  current_subtotal: number | null;
  items: {
    sku_id: string;
    product_name: string;
    quantity: number;
    quote?: { price: Price | null; purchasable: boolean; reason: string };
  }[];
};
export type TurnBody = {
  turn_id: string;
  message: string;
  selected_product_id?: string;
  selected_sku_id?: string;
  product_ids?: string[];
  quantity?: number;
  expected_cart_version?: number;
  respond_to_action_id?: string;
  idempotency_key?: string;
};
export type TurnResponse = {
  request_id?: string;
  status: string;
  intent?: string;
  question?: string;
  items?: Candidate[];
  proposed_action?: Proposal;
  cart?: Cart;
  action_result?: string;
  comparison?: {
    dimensions: { code: string; name: string }[];
    products: { name: string; values: Record<string, unknown> }[];
  };
  handoff?: { status: string; destination?: { ref?: string } };
  errors?: { code: string }[];
};
export type Message = {
  id: string;
  role: "user" | "assistant";
  text?: string;
  response?: TurnResponse;
  pending?: boolean;
  error?: string;
  retry?: { path: string; body: TurnBody; sent: boolean };
};
export type History = {
  id: string;
  title: string;
  messages: Message[];
  expired?: boolean;
};
export type Identity = {
  access_token: string;
  csrf_token: string;
  user_id: string;
};
