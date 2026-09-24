<?php
/**
 * ENVÍO GRATIS para Tienda de Drones (WooCommerce).
 *
 * El envío es gratis cuando el TOTAL DEL CARRITO llega a $400.000 (y siempre en
 * Córdoba Capital), según lo configurado en WooCommerce → Ajustes → Envío.
 * Este código solo lo comunica:
 *  - Productos de $400.000 o más: cartel "ENVÍO GRATIS" junto al precio (en la
 *    lista y en la ficha) y sobre la imagen si la plantilla lo permite.
 *  - Productos de menos: en la ficha, "Envío gratis en compras desde $400.000".
 *  - Carrito y checkout: "Te faltan $X para tener ENVÍO GRATIS".
 *
 * Instalación: WPCode → Code Snippets → Add Snippet → "Add Your Custom Code"
 * → "PHP Snippet" → pegar este código (sin la primera línea "<?php") →
 * Auto Insert / Run Everywhere → Activo → Guardar. Si la tienda usa caché
 * (LiteSpeed, WP Rocket, etc.), vaciarla después de guardar.
 */

// Monto de compra desde el que el envío es gratis (igual que en WooCommerce → Envío).
if ( ! defined( 'DT_ENVIO_GRATIS_DESDE' ) ) {
	define( 'DT_ENVIO_GRATIS_DESDE', 400000 );
}

function dt_tiene_envio_gratis( $product ) {
	if ( ! $product instanceof WC_Product ) {
		return false;
	}
	$precio = (float) wc_get_price_to_display( $product );  // en variables, el precio "desde"
	return $precio >= DT_ENVIO_GRATIS_DESDE;
}

// 1) Cartel junto al precio: funciona con casi cualquier plantilla (lista y ficha).
add_filter( 'woocommerce_get_price_html', function ( $html, $product ) {
	if ( ( is_admin() && ! wp_doing_ajax() ) || ! dt_tiene_envio_gratis( $product ) ) {
		return $html;
	}
	return $html . ' <span class="dt-envio-chip">ENVÍO GRATIS</span>';
}, 20, 2 );

// 2) Cartel sobre la imagen (solo en plantillas que usan los lugares estándar de WooCommerce).
add_action( 'woocommerce_before_shop_loop_item_title', function () {
	global $product;
	if ( dt_tiene_envio_gratis( $product ) ) {
		echo '<span class="dt-envio-gratis">ENVÍO GRATIS</span>';
	}
}, 9 );
add_action( 'woocommerce_before_single_product_summary', function () {
	global $product;
	if ( dt_tiene_envio_gratis( $product ) ) {
		echo '<span class="dt-envio-gratis dt-en-ficha">ENVÍO GRATIS</span>';
	}
}, 19 );

// 3) En la ficha de productos más baratos: invitar a llegar al mínimo.
add_action( 'woocommerce_single_product_summary', function () {
	global $product;
	if ( $product instanceof WC_Product && ! dt_tiene_envio_gratis( $product ) ) {
		echo '<p class="dt-envio-texto">🚚 <strong>Envío gratis</strong> en compras desde ' .
			wp_kses_post( wc_price( DT_ENVIO_GRATIS_DESDE ) ) . ' · Córdoba Capital: siempre gratis</p>';
	}
}, 11 );

// 4) Carrito y checkout: cuánto falta para el envío gratis.
function dt_aviso_envio_gratis() {
	if ( ! function_exists( 'WC' ) || ! WC()->cart || WC()->cart->is_empty() ) {
		return;
	}
	$total  = (float) WC()->cart->get_displayed_subtotal() - (float) WC()->cart->get_discount_total();
	$falta  = DT_ENVIO_GRATIS_DESDE - $total;
	if ( $falta > 0 ) {
		wc_print_notice( sprintf(
			'🚚 ¡Te faltan <strong>%s</strong> para tener <strong>ENVÍO GRATIS</strong>! (En Córdoba Capital el envío ya es gratis.) <a class="button" href="%s">Seguir comprando</a>',
			wc_price( $falta ),
			esc_url( wc_get_page_permalink( 'shop' ) )
		), 'notice' );
	} else {
		wc_print_notice( '🚚 ¡Tu compra tiene <strong>ENVÍO GRATIS</strong>!', 'success' );
	}
}
add_action( 'woocommerce_before_cart', 'dt_aviso_envio_gratis' );
add_action( 'woocommerce_before_checkout_form', 'dt_aviso_envio_gratis', 5 );

// Estilos (color, tamaño y posición se cambian acá).
add_action( 'wp_head', function () {
	?>
	<style>
		.dt-envio-chip {
			display: inline-block; vertical-align: middle; margin-left: 6px;
			background: #0a7d3b; color: #fff; font-weight: 700; font-size: 11px;
			line-height: 1; padding: 4px 7px; border-radius: 4px; letter-spacing: .4px; white-space: nowrap;
		}
		ul.products li.product { position: relative; }
		.single-product div.product { position: relative; }
		.dt-envio-gratis {
			position: absolute; top: 10px; right: 10px; z-index: 9;
			background: #0a7d3b; color: #fff; font-weight: 700; font-size: 12px;
			line-height: 1; padding: 6px 9px; border-radius: 4px; letter-spacing: .5px;
			pointer-events: none;
		}
		.dt-envio-gratis.dt-en-ficha { top: 15px; left: 15px; right: auto; font-size: 14px; }
		.dt-envio-texto { color: #0a7d3b; margin: 6px 0 14px; font-size: 15px; }
	</style>
	<?php
} );
